"""Deterministic physical-layout builders; never modify earlier experiments."""
from common import *
import argparse
import itertools
import shutil
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ALL_METHODS = ['W', 'C', 'Cstar', 'T', 'F', 'T_time', 'Tperm_time',
               'Tperm_blocksv', 'C_2048', 'C_32768', 'C_full', 'W_packed']
CORE_CONFIGS = {
    'W': dict(family='W', order='time', rowgroup=8192),
    'C': dict(family='C', order='time', rowgroup=8192),
    'T': dict(family='T', order='entity', minutes=120),
    'F': dict(family='F', order='entity', minutes=120),
}


def permutation_plan(lengths):
    """Maximize total absolute internal-cut ROW displacement; smallest shift wins."""
    lengths = list(map(int, lengths))
    assert lengths and all(n > 0 for n in lengths)
    original = np.cumsum(lengths, dtype='int64')[:-1]
    best_shift, best_score, best = 0, 0, lengths
    for shift in range(1, len(lengths)):
        candidate = lengths[shift:] + lengths[:shift]
        score = int(np.abs(np.cumsum(candidate, dtype='int64')[:-1] - original).sum())
        if score > best_score:
            best_shift, best_score, best = shift, score, candidate
    return dict(original_lengths=lengths, permuted_lengths=best, shift=best_shift,
                cumulative_cut_row_displacement=best_score, changed=best_score > 0,
                criterion='maximum sum absolute internal cumulative row-cut displacement; smallest positive shift tie-break; unchanged if all scores zero')


def metric(directory):
    directory = Path(directory)
    catalog = load(directory / 'catalog.json')
    return dict(path=str(directory.resolve()), files=len(catalog),
                row_groups=sum(e['row_groups'] for e in catalog),
                rows=sum(e['rows'] for e in catalog),
                parquet_bytes=sum(e['bytes'] for e in catalog),
                total_bytes=sum(p.stat().st_size for p in directory.iterdir() if p.is_file()),
                catalog_sha256=sha(directory / 'catalog.json'))


def register(path, source, codes, system=None):
    md = pq.read_metadata(path)
    out = dict(file=path.name, source_id=source['id'], bytes=path.stat().st_size,
               sha256=sha(path), rows=md.num_rows, row_groups=md.num_row_groups,
               codes=list(codes))
    if system is not None:
        out['system'] = system
    for field, col in [('source_id', 0), ('t', 2), ('sv', 3)]:
        stats = [md.row_group(i).column(col).statistics for i in range(md.num_row_groups)]
        assert stats and all(x is not None and x.has_min_max for x in stats)
        low, high = int(min(x.min for x in stats)), int(max(x.max for x in stats))
        if field == 'source_id':
            assert low == high == source['id']
        else:
            out[field + '_min'], out[field + '_max'] = low, high
    return out


def blocks_for(tab, source, order):
    day = s.epoch_base(source)
    blocks = []
    for start in range(day, day + 86400000000, 7200000000):
        block = tab.filter(pc.and_(pc.greater_equal(tab['t'], start), pc.less(tab['t'], start + 7200000000)))
        if len(block):
            blocks.append((start, s.sort_table(block, order)))
    return blocks


def write_groups(path, table, lengths):
    offset = 0
    with s.writer(path, table.schema) as writer:
        for n in lengths:
            writer.write_table(table.slice(offset, n), row_group_size=n)
            offset += n
    assert offset == len(table)


def build_source(dest, method, source):
    if method in CORE_CONFIGS:
        return s.build_source(dest, CORE_CONFIGS[method], source), []
    arrays = s.arrays(source)
    entries, plans = [], []
    packed_writer = None
    packed_path = dest / (source['name'] + '_all.parquet')
    try:
        for system in sorted(source['systems']):
            indices = np.flatnonzero(arrays['sv'] // 100 == ord(system))
            codes = source['codes'] if method in ['C_full', 'W_packed'] else sorted(source['schema'][system])
            tab = s.table_for(source, arrays, indices, codes)
            path = dest / (source['name'] + '_' + system + '.parquet')
            if method in ['C_full', 'W_packed', 'C_2048', 'C_32768']:
                tab = s.sort_table(tab, 'time')
                size = {'C_2048': 2048, 'C_32768': 32768}.get(method, 8192)
                if method == 'W_packed':
                    if packed_writer is None:
                        packed_writer = s.writer(packed_path, tab.schema)
                    # Each call creates exactly the same groups as this system's C_full file.
                    packed_writer.write_table(tab, row_group_size=size)
                else:
                    with s.writer(path, tab.schema) as writer:
                        writer.write_table(tab, row_group_size=size)
            else:
                order = 'time' if method in ['T_time', 'Tperm_time'] else 'entity'
                blocks = blocks_for(tab, source, order)
                tab = pa.concat_tables([block for _, block in blocks])
                lengths = [len(block) for _, block in blocks]
                if method == 'Cstar':
                    with s.writer(path, tab.schema) as writer:
                        writer.write_table(tab, row_group_size=8192)
                elif method == 'T_time':
                    write_groups(path, tab, lengths)
                else:
                    plan = permutation_plan(lengths)
                    old_cuts = np.cumsum(lengths, dtype='int64')[:-1]
                    new_cuts = np.cumsum(plan['permuted_lengths'], dtype='int64')[:-1]
                    times = tab['t'].to_numpy()
                    plan.update(file=path.name, source_id=source['id'], system=system, rows=len(tab),
                                original_cut_rows=old_cuts.tolist(), new_cut_rows=new_cuts.tolist(),
                                cut_time_displacement_seconds=[abs(int(times[j])-int(times[i]))/1000000 for i, j in zip(old_cuts, new_cuts)])
                    plans.append(plan)
                    write_groups(path, tab, plan['permuted_lengths'])
            if method != 'W_packed':
                entries.append(register(path, source, codes, system))
    finally:
        if packed_writer is not None:
            packed_writer.close()
    if method == 'W_packed':
        entries.append(register(packed_path, source, source['codes']))
    return entries, plans


def reuse_subset(source_directory, destination, manifest):
    ids = {s['id'] for s in manifest['sources']}
    selected = [e for e in load(source_directory / 'catalog.json') if e['source_id'] in ids]
    assert {e['source_id'] for e in selected} == ids
    for e in selected:
        path = source_directory / e['file']
        assert path.stat().st_size == e['bytes'] and sha(path) == e['sha256']
        shutil.copy2(path, destination / e['file'])
    return selected


def build(dataset, methods):
    assert set(methods) <= set(ALL_METHODS), methods
    folder = ROOT / 'inputs' / dataset
    manifest = load(folder / 'sources.json')
    layout_paths = load(folder / 'layouts.json') if (folder / 'layouts.json').exists() else {}
    metrics = load(folder / 'layout_metrics.json') if (folder / 'layout_metrics.json').exists() else {}
    origins = load(folder / 'layout_origins.json') if (folder / 'layout_origins.json').exists() else {}
    for method in methods:
        if method in layout_paths:
            directory = Path(layout_paths[method])
            assert directory.exists(), directory
            for e in load(directory / 'catalog.json'):
                p = directory / e['file']
                assert p.stat().st_size == e['bytes'] and sha(p) == e['sha256']
            metrics[method] = metric(directory)
            dump(folder / 'layout_metrics.json', metrics)
            print('REUSE VERIFIED LAYOUT', dataset, method, flush=True)
            continue
        destination = ROOT / 'layouts' / dataset / method
        staging = destination.with_name(method + '.building')
        assert not destination.exists() and not staging.exists(), f'Existing unregistered build requires review: {destination}'
        staging.mkdir(parents=True)
        plans = []
        if method in origins:
            entries = reuse_subset(Path(origins[method]), staging, manifest)
        else:
            entries = []
            for source in manifest['sources']:
                added, source_plans = build_source(staging, method, source)
                entries.extend(added); plans.extend(source_plans)
                print('BUILD', dataset, method, source['name'], len(added), 'files', flush=True)
        dump(staging / 'sources.json', manifest)
        config = dict(name=method, family=method, writer='Parquet 2.6/Zstd3/dictionary-nonfloat/byte-stream-split-float/statistics',
                      source_schema='station-day full schema' if method in ['W', 'C_full', 'W_packed'] else 'station-day constellation-local schema')
        dump(staging / 'layout.json', dict(config=config, files=entries))
        dump(staging / 'catalog.json', entries)
        if plans:
            total_rows = sum(x['rows'] for x in plans)
            changed_rows = sum(x['rows'] for x in plans if x['changed'])
            coverage = changed_rows / total_rows
            dump(staging / 'boundary_plan.json', dict(files=plans, total_rows=total_rows,
                 changed_file_rows=changed_rows, changed_file_row_coverage=coverage, required_coverage=0.8,
                 intervention_gate_passed=coverage >= 0.8,
                 interpretation='Matched row-count multiset and stream; boundaries, memberships, statistics and compression may change together.'))
        staging.rename(destination)
        layout_paths[method] = str(destination.resolve()); metrics[method] = metric(destination)
        dump(folder / 'layouts.json', layout_paths); dump(folder / 'layout_metrics.json', metrics)
        print('BUILT', dataset, method, metrics[method]['files'], 'files', flush=True)
    return layout_paths


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build'])
    parser.add_argument('--dataset', choices=['D0m', 'D1', 'D2', 'D2m'], required=True)
    parser.add_argument('--methods', nargs='+', choices=ALL_METHODS)
    args = parser.parse_args()
    defaults = ALL_METHODS if args.dataset in ['D0m', 'D2m'] else CORE
    build(args.dataset, args.methods or defaults)
