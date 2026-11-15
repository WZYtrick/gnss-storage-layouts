"""Independent, read-only audit of the completed continuation and its inputs.

This program does not import or call the experiment runner or any reader.
It checks raw log coverage, frozen order, provenance, hashes and all built files.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
import random
import sys

ROOT = Path(__file__).resolve().parent
CAMPAIGN = ROOT / 'campaigns/revision_v1_20261112'
SESSION = ROOT / 'sessions/resume_until_1100_20261113'
METHODS = ['W', 'C', 'Cstar', 'T', 'F', 'GeoRinex_R', 'GeoRinex_H']


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def check_entry(item):
    path = Path(item['path'])
    assert path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], path


def instant(value):
    return datetime.fromisoformat(value)


def main():
    design = load(CAMPAIGN / 'design.json')
    summary = load(SESSION / 'summary.json')
    auth = load(SESSION / 'authorization.json')
    finished = load(SESSION / 'finished.json')
    processes = load(SESSION / 'post_deadline_process_audit.json')
    assert processes['all_stopped'] and not processes['live_matching_processes']
    assert summary['state'] == 'stopped_at_authorized_block_boundary' and finished['active_child_exited']
    assert not (SESSION / 'failure.json').exists()
    assert (ROOT / 'resume_until_1100_20261113.stderr.log').stat().st_size == 0
    assert instant(summary['utc']) <= instant(finished['utc']) < instant(auth['deadline_utc'])
    assert not summary['next_day_restart'] and (ROOT / 'STOP_AFTER_BLOCK').exists()
    assert sys.version.startswith('3.12.14')
    assert {name: importlib.metadata.version(name) for name in design['versions']} == design['versions']
    for name, value in design['runtime_code'].items():
        assert sha(ROOT / name) == value, name
    for item in design['input_files']:
        check_entry(dict(item, path=str(ROOT / item['path'])))
    check_entry(design['baseline_validation'])
    validation = load(design['baseline_validation']['path'])
    assert validation['all_passed'] and validation['queries'] == 864
    assert sha(ROOT / 'evidence/freezes/D2_predictions.json') == design['old_D2_predictions_sha256']
    for item in summary['completed_jobs']:
        check_entry(item['verification'])
    current_entries = {item['job'] for item in summary['completed_here']}
    all_entries = {item['job'] for item in summary['completed_jobs']}
    assert len(current_entries) == 17 and len(all_entries) == 25
    assert {p.name for p in (CAMPAIGN / 'runs').iterdir() if p.is_dir()} == all_entries

    authorizations = {}
    datasets = {}
    source_files = set()
    for dataset in ['D0m', 'D1', 'D2', 'D2m']:
        folder = ROOT / 'inputs' / dataset
        manifest = load(folder / 'sources.json')
        datasets[dataset] = dict(manifest=manifest, metrics=load(folder / 'layout_metrics.json'),
                                 paths=load(folder / 'layouts.json'), truth=load(folder / 'truth.json'),
                                 queries=load(folder / 'queries.json'))
        for source in manifest['sources']:
            for kind in ['source', 'compressed']:
                path = Path(source[kind + '_file'])
                if str(path) not in source_files:
                    assert path.stat().st_size == source[kind + '_bytes']
                    assert sha(path) == source[kind + '_sha256']
                    source_files.add(str(path))
    print('Frozen code, input manifests, source archives and original 864-query validation verified.', flush=True)

    jobs = {job['id']: job for job in design['jobs']}
    details = []
    baseline_records = []
    build_counts = Counter()
    file_count = byte_count = 0
    for name in sorted(all_entries):
        out = CAMPAIGN / 'runs' / name
        assert not (out / 'failure.json').exists()
        certificate = load(out / 'verification.json')
        execution = load(out / 'session_execution.json')
        assert certificate['all_passed'] and execution['all_passed']
        assert execution['job'] == jobs[name] and execution['process'] == certificate['process']
        session = certificate['execution_session']
        assert execution['session'] == session
        if session not in authorizations:
            afile = ROOT / 'sessions' / session / 'authorization.json'
            record = load(afile)
            assert record['original_design_sha256'] == sha(CAMPAIGN / 'design.json')
            assert record['unchanged_runtime_code'] == design['runtime_code']
            authorizations[session] = record
        actual_auth = authorizations[session]
        for item in [certificate['authorization'], certificate['session_wrapper'], execution['authorization']]:
            check_entry(item)
        assert Path(certificate['authorization']['path']).resolve() == (ROOT / 'sessions' / session / 'authorization.json').resolve()
        assert certificate['session_wrapper']['sha256'] == actual_auth['wrapper_sha256']
        assert certificate['time_policy'] == execution['time_policy'] == actual_auth['time_policy']
        begin, end = instant(certificate['started_utc']), instant(execution['ended_utc'])
        assert instant(actual_auth['started_utc']) <= begin <= instant(certificate['ended_utc']) <= end < instant(actual_auth['deadline_utc'])
        for filename, digest in certificate['code'].items():
            assert digest == design['runtime_code'][filename]
        ds, block = certificate['dataset'], certificate['block']
        assert ds == jobs[name]['dataset'] and block == jobs[name]['block']
        data = datasets[ds]
        if jobs[name]['kind'] == 'baseline':
            assert name == 'baseline_D1_b00'
            query_by_id = {q['id']: q for q in data['queries']}
            expected_order = []
            generator = random.Random(280112 + block)
            for repeat in [-1, 0, 1, 2]:
                queries = list(data['queries'])
                generator.shuffle(queries)
                for q in queries:
                    methods = sorted(METHODS, key=lambda method: hashlib.sha256(f'280112:{block}:{repeat}:{q["id"]}:{method}'.encode()).digest())
                    expected_order.extend((q['id'], method, repeat) for method in methods)
            with (out / 'timings.jsonl').open(encoding='utf-8') as stream:
                rows = [json.loads(line) for line in stream]
            assert len(rows) == 6048 and len(expected_order) == len(rows)
            seen = set()
            previous_end = begin
            for position, (row, expected) in enumerate(zip(rows, expected_order)):
                key = row['query'], row['method'], row['repeat']
                assert key == expected and key not in seen
                seen.add(key)
                assert row['call_order'] == position and row['process'] == certificate['process']
                assert row['dataset'] == ds and row['block'] == block
                assert row['family'] == query_by_id[row['query']]['family']
                assert row['warmup'] == (row['repeat'] == -1)
                assert row['cpu'] == 1 and row['io'] == 4 and row['contract'] == 'ordered_dense'
                assert row['time_policy'] == actual_auth['time_policy'] and row['execution_session'] == session
                assert row['authorization_sha256'] == certificate['authorization']['sha256']
                assert row['session_wrapper_sha256'] == actual_auth['wrapper_sha256']
                assert row['rows'] == data['truth'][row['query']]['rows'] and row['sha256'] == data['truth'][row['query']]['sha256']
                start_call, end_call = instant(row['started_utc']), instant(row['ended_utc'])
                assert previous_end <= start_call <= end_call <= end
                previous_end = end_call
                for field, value in row.items():
                    if field == 'seconds' or field.endswith('_seconds'):
                        assert math.isfinite(value) and value >= 0, (position, field)
                assert math.isclose(row['seconds'], row['scan_seconds'] + row['normalize_seconds'] + row.get('overhead_seconds', 0), abs_tol=1e-7, rel_tol=1e-8)
            assert sum(not r['warmup'] for r in rows) == certificate['formal_calls'] == 4536
            assert sum(r['warmup'] for r in rows) == certificate['warmup_calls'] == 1512
            call_audit = load(out / 'session_call_audit.json')
            assert call_audit['all_passed'] and call_audit['unique_call_keys'] == 6048
            check_entry(call_audit['timings'])
            baseline_records = rows
            print('D1 baseline: all 6048 call records match frozen order, truth digests, identities and authorization.', flush=True)
        else:
            sources = {source['id']: source for source in data['manifest']['sources']}
            assert certificate['purpose'] == 'formal' and certificate['sources'] == len(sources)
            assert set(certificate['methods']) == set(data['metrics'])
            assert len(certificate['results']) == len(data['metrics'])
            assert {row['method'] for row in certificate['results']} == set(data['metrics'])
            for row in certificate['results']:
                method = row['method']
                original = data['metrics'][method]
                assert row['dataset'] == ds and row['block'] == block
                for field in ['rows', 'files', 'row_groups', 'parquet_bytes']:
                    assert row[field] == original[field], (name, method, field)
                assert row['verified_rows'] == sum(source['records'] for source in sources.values())
                full = row['full_source_hashes']
                assert len(full) == len(sources) and {r['source_id'] for r in full} == set(sources)
                assert all(r['rows'] == sources[r['source_id']]['records'] for r in full)
                for field in ['decompress_seconds', 'parse_seconds', 'build_seconds', 'init_seconds']:
                    assert math.isfinite(row[field]) and row[field] >= 0
                assert math.isclose(row['from_rnx_seconds'], sum(row[f] for f in ['parse_seconds', 'build_seconds', 'init_seconds']), rel_tol=1e-14)
                assert math.isclose(row['from_crx_seconds'], row['decompress_seconds'] + row['from_rnx_seconds'], rel_tol=1e-14)
                folder = out / 'layouts' / method
                assert sha(folder / 'catalog.json') == row['catalog_sha256']
                catalog = load(folder / 'catalog.json')
                old_catalog = load(Path(data['paths'][method]) / 'catalog.json')
                comparable = lambda x: (x['source_id'], x['sha256'], x['bytes'])
                assert sorted(map(comparable, catalog)) == sorted(map(comparable, old_catalog))
                assert len(catalog) == row['files'] and sum(x['bytes'] for x in catalog) == row['parquet_bytes']
                for item in catalog:
                    path = folder / item['file']
                    assert path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], path
                    file_count += 1
                    byte_count += item['bytes']
            build_counts[ds] += len(certificate['results'])
            print(f'Build {name}: {len(certificate["results"])} layouts and all file hashes verified.', flush=True)
        details.append(dict(job=name, kind=jobs[name]['kind'], dataset=ds, block=block, session=session,
                            started_utc=certificate['started_utc'], ended_utc=execution['ended_utc'],
                            elapsed_seconds=(end - begin).total_seconds(), verification_sha256=sha(out / 'verification.json')))

    assert build_counts == dict(D0m=72, D1=30, D2=30, D2m=72)
    assert len(baseline_records) == 6048
    for ds in build_counts:
        assert {r['block'] for r in details if r['kind'] == 'construction' and r['dataset'] == ds} == set(range(6))
    ordered = sorted(details, key=lambda r: instant(r['started_utc']))
    assert all(instant(ordered[i]['ended_utc']) <= instant(ordered[i+1]['started_utc']) for i in range(len(ordered) - 1))
    assert {r['job'] for r in details if r['session'] == auth['session']} == current_entries
    pending = [job for job in design['jobs'] if job['id'] not in all_entries]
    assert [job['id'] for job in pending] == summary['remaining_jobs']
    spare = (instant(auth['deadline_utc']) - instant(summary['utc'])).total_seconds()
    assert all(job['scheduling_seconds'] + 30 > spare for job in pending)
    assert summary['layout_builds'] == 204 and summary['formal_query_calls'] == 4536 and summary['warmup_query_calls'] == 1512
    measured = next(r['elapsed_seconds'] for r in details if r['job'] == 'baseline_D1_b00')
    projected = sum(measured * design['budget'][j['dataset']]['estimated_block_seconds'] / design['budget']['D1']['estimated_block_seconds'] for j in pending)
    frozen = sum(design['budget'][j['dataset']]['estimated_block_seconds'] for j in pending)
    current_details = [r for r in details if r['session'] == auth['session']]
    result = dict(all_passed=True, utc=datetime.now(timezone.utc).isoformat(), verification_kind='independent raw-log and file-integrity audit; no measurements rerun',
                  session_started_utc=auth['started_utc'], stopped_utc=summary['utc'], deadline_utc=auth['deadline_utc'],
                  stopped_before_deadline=True, all_processes_stopped=True, current_session_failures=0,
                  current_session_blocks=len(current_details), current_session_new_builds=136,
                  completed_baseline_blocks=1, completed_construction_blocks=24, completed_layout_builds=204,
                  layout_builds_by_dataset=dict(build_counts), formal_calls=4536, warmup_calls=1512,
                  original_georinex_validation_queries=864, raw_call_records_independently_checked=6048,
                  parquet_files_rehashed=file_count, parquet_bytes_rehashed=byte_count, source_files_rehashed=len(source_files),
                  original_runtime_and_D2_predictions_unchanged=True,
                  remaining_baseline_blocks=dict(D1=5, D2=6), remaining_formal_calls=54432-4536, remaining_warmup_calls=18144-1512,
                  remaining_builds=0, baseline_observed_hours=measured/3600,
                  current_construction_hours=sum(r['elapsed_seconds'] for r in current_details if r['kind']=='construction')/3600,
                  remaining_hours_observed_projection=projected/3600, remaining_hours_frozen_estimate=frozen/3600,
                  remaining_nightly_windows_if_0100_0800=11, remaining_jobs=[j['id'] for j in pending],
                  analysis_status='Final baseline comparison and amortization require the remaining 11 paired blocks and authorization-aware analysis.',
                  details=details)
    target = SESSION / 'post_deadline_independent_audit.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'details'}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
