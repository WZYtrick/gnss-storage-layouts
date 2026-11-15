"""Three output contracts over the same Arrow scan; validation stays outside timing."""
from pathlib import Path
import time
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from common import s, CONTRACTS

SORT_KEYS = [('source_id', 'ascending'), ('rid', 'ascending')]


def configure_pools(cpu=8, io=4):
    # storage imports historically set both pools to one: configure after imports.
    pa.set_cpu_count(cpu)
    pa.set_io_thread_count(io)
    assert pa.cpu_count() == cpu and pa.io_thread_count() == io
    return {'cpu': pa.cpu_count(), 'io': pa.io_thread_count()}


def expression(q):
    expr = ds.field('source_id').isin(q['source_ids']) & (ds.field('t') >= q['start']) & (ds.field('t') < q['end'])
    if q.get('systems') is not None:
        terms = [(ds.field('sv') >= ord(x) * 100) & (ds.field('sv') < ord(x) * 100 + 100) for x in q['systems']]
        if not terms:
            expr = expr & ds.scalar(False)
        else:
            clause = terms[0]
            for term in terms[1:]:
                clause = clause | term
            expr = expr & clause
    if q.get('satellite') is not None:
        expr = expr & (ds.field('sv') == q['satellite'])
    return expr, s.KEYS + [f'{kind}_{code}' for code in q['codes'] for kind in ('v', 'l', 's')]


def _dense(tab, codes, ordered, marks=None):
    """Both timing modes execute this helper with identical table lifetimes."""
    if ordered:
        idx = pc.sort_indices(tab, sort_keys=SORT_KEYS)
        if marks is not None:
            marks.append(('sort_indices', time.perf_counter_ns()))
        tab = tab.take(idx)
        del idx
        if marks is not None:
            marks.append(('take', time.perf_counter_ns()))
    out = {k: tab[k].to_numpy().astype(d, copy=False) for k, d in zip(s.KEYS, s.DTYPES)}
    if marks is not None:
        marks.append(('key_conversion', time.perf_counter_ns()))
    for kind, dtype, fill in [('v', 'float64', np.nan), ('l', 'int8', -1), ('s', 'int8', -1)]:
        vectors = [pc.fill_null(tab[f'{kind}_{code}'], fill).to_numpy().astype(dtype, copy=False) for code in codes]
        if marks is not None:
            marks.append((kind + '_fill_convert', time.perf_counter_ns()))
        out[kind] = np.column_stack(vectors) if vectors else np.empty((len(tab), 0), dtype=dtype)
        del vectors
        if marks is not None:
            marks.append((kind + '_stack', time.perf_counter_ns()))
    return out


class Reader(s.Reader):
    def __init__(self, directory, manifest, cpu=8):
        self.cpu = cpu
        self.pools = configure_pools(cpu)
        super().__init__(Path(directory), manifest)
        assert pa.cpu_count() == cpu and pa.io_thread_count() == 4

    def query(self, q, contract='ordered_dense', profile=False):
        if contract not in CONTRACTS:
            raise ValueError('Unknown output contract: ' + str(contract))
        assert pa.cpu_count() == self.cpu and pa.io_thread_count() == 4, 'Arrow pools changed after Reader construction'
        start = time.perf_counter_ns()
        marks = [] if profile else None
        expr, cols = expression(q)
        if marks is not None:
            marks.append(('predicate_projection', time.perf_counter_ns()))
        scanner = self.dataset.scanner(columns=cols, filter=expr, use_threads=True,
            batch_size=131072, batch_readahead=16, fragment_readahead=4,
            cache_metadata=True, fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=True))
        if marks is not None:
            marks.append(('scanner_create', time.perf_counter_ns()))
        tab = scanner.to_table()
        del scanner
        scanned = time.perf_counter_ns()
        if marks is not None:
            marks.append(('to_table', scanned))
        if contract == 'unordered_arrow':
            out = tab
        else:
            out = _dense(tab, q['codes'], contract == 'ordered_dense', marks)
        end = time.perf_counter_ns()
        timing = dict(seconds=(end - start) / 1e9, scan_seconds=(scanned - start) / 1e9,
            normalize_seconds=(end - scanned) / 1e9)
        if marks is not None:
            marks.append(('arrow_return' if contract == 'unordered_arrow' else 'normalize_finalize', end))
            previous = start
            stages_ns = {}
            for name, tick in marks:
                stages_ns[name] = tick - previous
                previous = tick
            timing['stages'] = {name: ns / 1e9 for name, ns in stages_ns.items()}
            timing['stages_ns'] = stages_ns
            timing['seconds_ns'] = end - start
            assert sum(stages_ns.values()) == end - start
        return out, timing


def canonical(result, codes, contract):
    """Canonical dense representation, explicitly excluded from measured calls."""
    if contract not in CONTRACTS:
        raise ValueError(contract)
    if contract == 'unordered_arrow':
        return _dense(result, codes, True)
    order = np.lexsort((result['rid'], result['source_id']))
    return {key: result[key][order] for key in s.KEYS + ['v', 'l', 's']}


def fingerprint_result(result, q, contract):
    if contract == 'ordered_dense':
        # Order is part of this contract: canonicalizing here would hide a defect.
        return s.fingerprint(result)
    return s.fingerprint(canonical(result, q['codes'], contract))


def assert_result(result, q, contract, expected):
    actual = canonical(result, q['codes'], contract)
    expected_hash = expected['sha256'] if 'sha256' in expected else s.fingerprint(expected)
    expected_rows = expected['rows'] if 'sha256' in expected else len(expected['rid'])
    assert len(actual['rid']) == expected_rows
    assert s.fingerprint(actual) == expected_hash, (q.get('id'), contract, 'result fingerprint mismatch')
    if contract == 'ordered_dense':
        assert np.array_equal(result['rid'], actual['rid']) and np.array_equal(result['source_id'], actual['source_id'])
    return {'rows': expected_rows, 'sha256': expected_hash}
