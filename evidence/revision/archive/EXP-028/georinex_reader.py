"""Unmodified GeoRinex numeric parsing plus a timed identity/flag adapter.

No observation value is parsed by the adapter. Compressed inputs are expanded
once per call, then the same text is used by GeoRinex and the adapter. No parsed
records, headers, or numeric arrays are cached between calls.
"""
from common import s
from pathlib import Path
from decimal import Decimal
import datetime as dt
import io
import time
import warnings
import numpy as np
import georinex as gr
import hatanaka

# Xarray 2026.9 repeats this warning inside each epoch merge. Keep its current
# semantics; suppress only the documented future-default notice, not errors.
warnings.filterwarnings('ignore', message='In a future version of xarray the default value.*',
                        category=FutureWarning, module=r'georinex\.obs3')

class UnsupportedSemantics(ValueError):
    pass

def identity_flags(stream, source_id, query):
    types, counts = {}, {}
    system = None
    for line in stream:
        if line[60:].strip() == 'SYS / # / OBS TYPES':
            if line[0] != ' ':
                system = line[0]
                types[system], counts[system] = [], int(line[3:6])
            types[system].extend(line[7:60].split())
        if line[60:].strip() == 'END OF HEADER':
            break
    else:
        raise UnsupportedSemantics('Missing header terminator')
    if not types or any(len(types[k]) != counts[k] for k in types):
        raise UnsupportedSemantics('Invalid observation schema')
    selected = {sys: [(i, query['codes'].index(c)) for i,c in enumerate(codes) if c in query['codes']]
                for sys,codes in types.items()}
    capacity, count, rid, pending = 8192, 0, 0, None
    out = {k: np.empty(capacity, dtype=d) for k,d in zip(s.KEYS,s.DTYPES)}
    out.update(l=np.full((capacity,len(query['codes'])),-1,dtype='int8'),
               s=np.full((capacity,len(query['codes'])),-1,dtype='int8'))
    seen = set()
    while True:
        line = pending if pending is not None else stream.readline()
        pending = None
        if not line:
            break
        if not line.startswith('>'):
            raise UnsupportedSemantics('Missing epoch marker')
        fields = line[1:].split()
        if len(fields) < 8:
            raise UnsupportedSemantics('Incomplete epoch')
        seconds = Decimal(fields[5]); micros = seconds * 1000000
        if micros != int(micros) or not 0 <= seconds < 60:
            raise UnsupportedSemantics('Unsupported epoch precision or leap second')
        timestamp = (dt.datetime(*map(int,fields[:5])) - dt.datetime(1970,1,1)) // dt.timedelta(microseconds=1) + int(micros)
        flag, n = int(fields[6]), int(fields[7])
        if flag not in (0,1):
            raise UnsupportedSemantics('GeoRinex baseline requires observation epochs (flags 0/1)')
        if timestamp >= query['end']:
            break
        for _ in range(n):
            line = pending if pending is not None else stream.readline()
            pending = None
            satellite = line[:3]
            if len(satellite)!=3 or satellite[0] not in types or not satellite[1:].strip().isdigit():
                raise UnsupportedSemantics('Invalid satellite record')
            payload = line[3:].rstrip('\r\n')
            while True:
                next_line = stream.readline()
                if next_line.startswith('   '):
                    payload += next_line[3:].rstrip('\r\n')
                else:
                    pending = next_line
                    break
            sv = ord(satellite[0])*100 + int(satellite[1:])
            keep = query['start'] <= timestamp and (query['systems'] is None or satellite[0] in query['systems']) and (query['satellite'] is None or query['satellite']==sv)
            if keep:
                identity = (timestamp,sv)
                if identity in seen:
                    raise UnsupportedSemantics('Duplicate epoch/satellite cannot be recovered from the GeoRinex array')
                seen.add(identity)
                if count == capacity:
                    for k in s.KEYS:
                        out[k] = np.concatenate([out[k], np.empty(capacity,dtype=out[k].dtype)])
                    for k in ['l','s']:
                        out[k] = np.concatenate([out[k], np.full_like(out[k],-1)])
                    capacity *= 2
                for k,value in zip(s.KEYS,[source_id,rid,timestamp,sv,flag]):
                    out[k][count] = value
                for i,j in selected[satellite[0]]:
                    cell = payload[i*16:(i+1)*16].ljust(16)
                    # Intentionally never interpret cell[:14] (observation value).
                    for k,index in [('l',14),('s',15)]:
                        if cell[index] != ' ':
                            if not cell[index].isdigit():
                                raise UnsupportedSemantics('Invalid LLI/SSI character')
                            out[k][count,j] = int(cell[index])
                count += 1
            rid += 1
    return {k:v[:count] for k,v in out.items()}

def attach_values(data, out, codes):
    if 'v' not in out:
        out['v'] = np.full((len(out['rid']),len(codes)),np.nan,dtype='float64')
    if not len(out['rid']):
        return out
    times = data.time.values.astype('datetime64[us]').astype('int64')
    satellites = np.array([ord(str(x)[0])*100 + int(str(x)[1:]) for x in data.sv.values],dtype='uint16')
    if len(set(map(int,times))) != len(times) or len(set(map(int,satellites))) != len(satellites):
        raise UnsupportedSemantics('Nonunique GeoRinex coordinates')
    ti = {int(x):i for i,x in enumerate(times)}
    si = {int(x):i for i,x in enumerate(satellites)}
    rows = np.array([i for i,(t,sv) in enumerate(zip(out['t'],out['sv'])) if int(t) in ti and int(sv) in si],dtype='int64')
    if len(rows):
        it = np.array([ti[int(out['t'][i])] for i in rows])
        iv = np.array([si[int(out['sv'][i])] for i in rows])
        for j,code in enumerate(codes):
            if code in data:
                out['v'][rows,j] = data[code].transpose('time','sv').values[it,iv]
    # A missing native coordinate is allowed only for an entirely unavailable
    # projected schema; the independent oracle validates every resulting null.
    return out

class GeoRinexReader:
    def __init__(self, manifest, compressed=False):
        begin = time.perf_counter()
        self.manifest, self.compressed = manifest, compressed
        self.init_seconds = time.perf_counter() - begin

    def query(self, query, contract='ordered_dense', profile=False):
        if contract != 'ordered_dense' or profile:
            raise ValueError('The revision baseline supports unprofiled ordered_dense only')
        start = time.perf_counter()
        parts = []
        phases = dict(decompress_seconds=0., parser_seconds=0., adapter_seconds=0.)
        wanted = set(query['source_ids'])
        tlim = [dt.datetime(1970,1,1)+dt.timedelta(microseconds=query['start']),
                dt.datetime(1970,1,1)+dt.timedelta(microseconds=query['end']-1)]
        for source in sorted((x for x in self.manifest['sources'] if x['id'] in wanted),key=lambda x:x['id']):
            # These guards describe the native parser's semantic coverage. They
            # never supply values or indexes to the query implementation.
            if source.get('events') or source.get('duplicate_epoch_sat_rows',0):
                raise UnsupportedSemantics('Source has events or duplicate records unsupported by this baseline')
            text_stream = None
            if self.compressed:
                at = time.perf_counter()
                content = hatanaka.decompress(Path(source['compressed_file']).read_bytes())
                text_stream = io.StringIO(content.decode('ascii'))
                del content
                phases['decompress_seconds'] += time.perf_counter()-at
                native_source = text_stream
            else:
                native_source = Path(source['source_file'])
            at = time.perf_counter()
            if text_stream is not None:
                text_stream.seek(0)
                out = identity_flags(text_stream, source['id'], query)
            else:
                with Path(source['source_file']).open(encoding='ascii', newline=None) as stream:
                    out = identity_flags(stream, source['id'], query)
            out['v'] = np.full((len(out['rid']),len(query['codes'])),np.nan,dtype='float64')
            # GeoRinex documents constellation selection. Use its native
            # single-system path, avoiding repeated cross-system xarray merges.
            # Systems are discovered by the timed raw identity scan, not truth.
            systems = sorted({chr(int(v)//100) for v in out['sv']})
            phases['adapter_seconds'] += time.perf_counter()-at
            for system in systems:
                at = time.perf_counter()
                data = gr.load(native_source, tlim=tlim, use={system}, meas=query['codes'],
                               useindicators=False, verbose=False)
                phases['parser_seconds'] += time.perf_counter()-at
                at = time.perf_counter()
                attach_values(data,out,query['codes'])
                del data
                phases['adapter_seconds'] += time.perf_counter()-at
            at = time.perf_counter()
            parts.append(out)
            del out
            if text_stream is not None:
                text_stream.close()
            phases['adapter_seconds'] += time.perf_counter()-at
        at = time.perf_counter()
        result = {k:np.concatenate([p[k] for p in parts]) for k in s.KEYS+['v','l','s']} if parts else s.empty(query['codes'])
        phases['adapter_seconds'] += time.perf_counter()-at
        elapsed = time.perf_counter()-start
        return result, dict(seconds=elapsed, **phases,
                            baseline_load_strategy='per_selected_constellation',
                            overhead_seconds=elapsed-sum(phases.values()),
                            scan_seconds=phases['decompress_seconds']+phases['parser_seconds'],
                            normalize_seconds=phases['adapter_seconds'])
