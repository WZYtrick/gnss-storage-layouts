"""Signal-series and declared-field missingness tasks, with independent scalar oracle."""
from collections import defaultdict
import datetime as dt
import hashlib
import math
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from common import s, CONTRACTS

TASKS = ['signal_series', 'missingness']
BIN_US = 300_000_000
SIGNAL_SCHEMA = pa.schema([
    ('station', pa.string()), ('sv', pa.uint16()), ('code', pa.string()),
    ('t', pa.int64()), ('source_id', pa.uint32()), ('rid', pa.uint32()),
    ('epoch_flag', pa.int8()), ('value', pa.float64()), ('lli', pa.int8()),
    ('ssi', pa.int8()), ('declared', pa.bool_())])
MISSING_SCHEMA = pa.schema([
    ('station', pa.string()), ('sv', pa.uint16()), ('code', pa.string()),
    ('bin_start', pa.int64()), ('declared_count', pa.int64()), ('missing_count', pa.int64()),
    ('valid_count', pa.int64()), ('undeclared_count', pa.int64()), ('missing_fraction', pa.float64())])


def signal_strength_units(manifest):
    """Copy source declarations literally; absence remains null, never inferred."""
    result = {}
    for src in manifest['sources']:
        found = [line[:60].strip() for line in src.get('header', []) if line[60:].strip() == 'SIGNAL STRENGTH UNIT']
        if len(set(found)) > 1:
            raise ValueError('Conflicting signal units in source ' + str(src['id']))
        result[str(src['id'])] = found[0] if found and found[0] else None
    return result


def make_requests(manifest, dates):
    dates = [s.source_date(x) for x in dates]
    if len(dates) != 2 or len(set(dates)) != 2:
        raise ValueError('Exactly two distinct calendar dates are required')
    stations = list(dict.fromkeys(src['station'] for src in manifest['sources']))
    if len(stations) != 6:
        raise ValueError('Exactly six stations are required')
    codes = [code for code in manifest['codes'] if code.startswith('S')]
    if not codes:
        raise ValueError('No signal-strength observation codes')
    units = signal_strength_units(manifest)
    queries = []
    for task in TASKS:
        for i, station in enumerate(stations):
            for date in dates:
                srcs = [src for src in manifest['sources'] if src['station'] == station and s.source_date(src) == date]
                if len(srcs) != 1:
                    raise ValueError(f'Expected one source for {station} {date}')
                for minutes in (30, 240, 1440):
                    base = s.epoch_base(date)
                    offset = 0 if minutes == 1440 else (3 * i * 3600 + (330 if minutes == 30 else 0)) * 1_000_000
                    queries.append(dict(id=f'{task}_{station}_{date.isoformat()}_{minutes}m', task=task,
                        station=station, date=date.isoformat(), window_minutes=minutes,
                        signal_strength_units={str(srcs[0]['id']): units[str(srcs[0]['id'])]},
                        time_basis='source_calendar', source_ids=[srcs[0]['id']],
                        start=base + offset, end=base + offset + minutes * 60_000_000,
                        codes=list(codes), systems=None, satellite=None))
    assert len(queries) == 72
    return queries


def as_arrow(result, codes, contract):
    if contract not in CONTRACTS:
        raise ValueError(contract)
    if contract == 'unordered_arrow':
        return result
    columns = {key: pa.array(result[key]) for key in s.KEYS}
    for j, code in enumerate(codes):
        columns['v_' + code] = pa.array(result['v'][:, j], mask=np.isnan(result['v'][:, j]), type=pa.float64())
        for kind in ('l', 's'):
            columns[kind + '_' + code] = pa.array(result[kind][:, j], mask=result[kind][:, j] < 0, type=pa.int8())
    return pa.table(columns)


def _base_context(tab, manifest):
    sources = {src['id']: src for src in manifest['sources']}
    source_ids = tab['source_id'].to_numpy()
    sv = tab['sv'].to_numpy()
    station = np.empty(len(tab), dtype=object)
    groups = []
    for sid in np.unique(source_ids):
        source_mask = source_ids == sid
        src = sources[int(sid)]
        station[source_mask] = src['station']
        for system in np.unique(sv[source_mask] // 100):
            groups.append((source_mask & (sv // 100 == system), set(src['schema'].get(chr(int(system)), []))))
    return pa.array(station, type=pa.string()), groups


def run_task(result, q, manifest, contract):
    """Caller times the entire function, including dense-to-Arrow adaptation."""
    tab = as_arrow(result, q['codes'], contract)
    task = q['task']
    if task not in TASKS:
        raise ValueError(task)
    if len(tab) == 0 or not q['codes']:
        return pa.Table.from_batches([], schema=SIGNAL_SCHEMA if task == 'signal_series' else MISSING_SCHEMA)
    station, groups = _base_context(tab, manifest)
    parts = []
    if task == 'signal_series':
        # Sort physical records before expansion; n-row sorting avoids sorting
        # n * number_of_codes rows while producing exactly the requested order.
        working = tab.append_column('_station', station)
        working = working.take(pc.sort_indices(working, sort_keys=[(key, 'ascending')
            for key in ['_station', 'sv', 't', 'source_id', 'rid']]))
        station_values = working['_station'].to_pylist()
        satellite_values = working['sv'].to_numpy()
        boundaries = [0] + [i for i in range(1, len(working)) if station_values[i] != station_values[i - 1]
            or satellite_values[i] != satellite_values[i - 1]] + [len(working)]
        sources = {src['id']: src for src in manifest['sources']}
        for start, end in zip(boundaries[:-1], boundaries[1:]):
            chunk = working.slice(start, end - start)
            ids = chunk['source_id'].to_numpy()
            system = chr(int(satellite_values[start]) // 100)
            for code in sorted(q['codes']):
                declared = np.zeros(len(chunk), dtype=bool)
                for sid in np.unique(ids):
                    if code in sources[int(sid)]['schema'].get(system, []):
                        declared[ids == sid] = True
                parts.append(pa.Table.from_arrays([chunk['_station'], chunk['sv'],
                    pa.array(np.full(len(chunk), code, dtype=object), type=pa.string()), chunk['t'], chunk['source_id'], chunk['rid'],
                    chunk['epoch_flag'], chunk['v_' + code], chunk['l_' + code], chunk['s_' + code], pa.array(declared)], schema=SIGNAL_SCHEMA))
        return pa.concat_tables(parts)
    if task == 'missingness':
        bins = pa.array((tab['t'].to_numpy() // BIN_US) * BIN_US, type=pa.int64())
    for code in q['codes']:
        declared = np.zeros(len(tab), dtype=bool)
        for mask, available in groups:
            if code in available:
                declared[mask] = True
        code_col = pa.array(np.full(len(tab), code, dtype=object), type=pa.string())
        if task == 'missingness':
            missing = declared & pc.is_null(tab['v_' + code]).to_numpy()
            counted = pa.table({'station': station, 'sv': tab['sv'], 'code': code_col, 'bin_start': bins,
                'declared_count': pa.array(declared.astype('int64')),
                'missing_count': pa.array(missing.astype('int64')),
                'undeclared_count': pa.array((~declared).astype('int64'))})
            grouped = counted.group_by(['station', 'sv', 'code', 'bin_start']).aggregate([
                ('declared_count', 'sum'), ('missing_count', 'sum'), ('undeclared_count', 'sum')])
            denominator = grouped['declared_count_sum'].to_numpy()
            numerator = grouped['missing_count_sum'].to_numpy()
            ratio = np.divide(numerator, denominator, out=np.zeros(len(grouped), dtype='float64'), where=denominator != 0)
            parts.append(pa.Table.from_arrays([grouped['station'], grouped['sv'], grouped['code'], grouped['bin_start'],
                grouped['declared_count_sum'], grouped['missing_count_sum'], pa.array(denominator - numerator), grouped['undeclared_count_sum'],
                pa.array(ratio, mask=denominator == 0)], schema=MISSING_SCHEMA))
    out = pa.concat_tables(parts)
    keys = ['station', 'sv', 'code', 't', 'source_id', 'rid'] if task == 'signal_series' else ['station', 'sv', 'code', 'bin_start']
    return out.take(pc.sort_indices(out, sort_keys=[(key, 'ascending') for key in keys]))


def reference_task(raw, q, manifest):
    """Independent Python scalar logic; raw must come from the independent reader.

    This deliberately does not call as_arrow, _base_context, run_task, or Arrow
    aggregation/sorting kernels. It retains each physical observation record.
    """
    sources = {int(src['id']): src for src in manifest['sources']}
    if q['task'] == 'signal_series':
        groups = defaultdict(list)
        for i in range(len(raw['rid'])):
            source = sources[int(raw['source_id'][i])]
            groups[(source['station'], int(raw['sv'][i]))].append(i)
        columns = {field.name: [] for field in SIGNAL_SCHEMA}
        for (station, satellite), indices in sorted(groups.items()):
            indices.sort(key=lambda i: (int(raw['t'][i]), int(raw['source_id'][i]), int(raw['rid'][i])))
            for j, code in sorted(enumerate(q['codes']), key=lambda pair: pair[1]):
                for i in indices:
                    sid = int(raw['source_id'][i])
                    value = float(raw['v'][i, j])
                    lli, ssi = int(raw['l'][i, j]), int(raw['s'][i, j])
                    row = (station, satellite, code, int(raw['t'][i]), sid, int(raw['rid'][i]), int(raw['epoch_flag'][i]),
                        None if math.isnan(value) else value, None if lli < 0 else lli, None if ssi < 0 else ssi,
                        code in sources[sid]['schema'].get(chr(satellite // 100), []))
                    for name, value in zip(columns, row):
                        columns[name].append(value)
        return pa.Table.from_pydict(columns, schema=SIGNAL_SCHEMA)
    if q['task'] != 'missingness':
        raise ValueError(q['task'])
    counts = defaultdict(lambda: [0, 0, 0])
    for i in range(len(raw['rid'])):
        source = sources[int(raw['source_id'][i])]
        satellite = int(raw['sv'][i])
        available = source['schema'].get(chr(satellite // 100), [])
        bucket = int(raw['t'][i]) // 300_000_000 * 300_000_000
        for j, code in enumerate(q['codes']):
            count = counts[(source['station'], satellite, code, bucket)]
            if code in available:
                count[0] += 1
                if math.isnan(float(raw['v'][i, j])):
                    count[1] += 1
            else:
                count[2] += 1
    rows = [(*key, value[0], value[1], value[0] - value[1], value[2],
        None if value[0] == 0 else value[1] / value[0]) for key, value in sorted(counts.items())]
    return pa.Table.from_arrays([pa.array([row[i] for row in rows], type=field.type) for i, field in enumerate(MISSING_SCHEMA)], schema=MISSING_SCHEMA)


def independent_reference(q, manifest):
    from direct_reader import DirectReader
    raw, _ = DirectReader(manifest, decimal_values=True).query(q)
    return reference_task(raw, q, manifest)


def task_fingerprint(table):
    """Chunk-independent digest with canonical null payloads and signed-zero fidelity."""
    h = hashlib.sha256(str(table.schema.remove_metadata()).encode())
    h.update(str(len(table)).encode())
    for field in table.schema:
        col = table[field.name].combine_chunks()
        validity = pc.is_valid(col).to_numpy(zero_copy_only=False)
        h.update(np.packbits(validity).tobytes())
        if pa.types.is_string(field.type):
            filled = pc.fill_null(col, '')
            offsets = np.frombuffer(filled.buffers()[1], dtype='<i4', count=len(filled) + 1, offset=filled.offset * 4)
            first, last = int(offsets[0]), int(offsets[-1])
            h.update((offsets - first).tobytes())
            if last > first:
                h.update(memoryview(filled.buffers()[2])[first:last])
        else:
            filled = pc.fill_null(col, False if pa.types.is_boolean(field.type) else 0)
            h.update(np.ascontiguousarray(filled.to_numpy(zero_copy_only=False)).tobytes())
    return h.hexdigest()


def assert_task_equal(actual, expected):
    assert actual.schema == expected.schema and len(actual) == len(expected)
    digest = task_fingerprint(actual)
    assert digest == task_fingerprint(expected), 'Task output mismatch (including nulls and signed zero)'
    return {'rows': len(actual), 'sha256': digest}
