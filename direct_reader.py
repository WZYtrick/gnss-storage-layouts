"""Independent projection-aware streaming RINEX reader, no prepared-array cache.

Record framing, header parsing and filtering are independent of storage.py.
The timed baseline uses float conversion; validation can use Decimal conversion.
"""
import datetime as dt
from decimal import Decimal
import io,time
from pathlib import Path
import numpy as np

KEYS=['source_id','rid','t','sv','epoch_flag']
DTYPES=['uint32','uint32','int64','uint16','int8']

class DirectReader:
    def __init__(self,manifest,compressed=False,decimal_values=False):
        self.manifest=manifest;self.compressed=compressed;self.decimal_values=decimal_values;self.init_seconds=0.0
    def query(self,q):
        begin=time.perf_counter();decomp=0.;parts=[];checked=0
        wanted=set(q['source_ids'])
        for src in sorted((x for x in self.manifest['sources'] if x['id'] in wanted),key=lambda x:x['id']):
            if self.compressed:
                import hatanaka
                at=time.perf_counter();content=hatanaka.decompress(Path(src['compressed_file']).read_bytes());decomp+=time.perf_counter()-at
                stream=io.TextIOWrapper(io.BytesIO(content),encoding='ascii',newline=None)
            else:stream=open(src['source_file'],encoding='ascii',newline=None)
            with stream as f:
                types={};counts={};system=None
                while True:
                    line=f.readline().rstrip('\r\n')
                    if not line:raise ValueError('Missing RINEX header terminator')
                    if line[60:].strip()=='SYS / # / OBS TYPES':
                        if line[0]!=' ':system=line[0];types[system]=[];counts[system]=int(line[3:6])
                        types[system]+=[line[j:j+3].strip() for j in range(7,59,4) if line[j:j+3].strip()]
                    if line[60:].strip()=='END OF HEADER':break
                if any(len(types[k])!=v for k,v in counts.items()):raise ValueError('Observation schema count mismatch')
                selected={system:[(types[system].index(c),j) for j,c in enumerate(q['codes']) if c in types[system]] for system in types}
                cap=8192;nout=0;rid=0;pending=None;nc=len(q['codes'])
                a={k:np.empty(cap,dtype=d) for k,d in zip(KEYS,DTYPES)}
                a.update(v=np.full((cap,nc),np.nan),l=np.full((cap,nc),-1,dtype='int8'),s=np.full((cap,nc),-1,dtype='int8'))
                while True:
                    line=pending if pending is not None else f.readline();pending=None
                    if not line:break
                    if not line.startswith('>'):raise ValueError('Missing epoch marker')
                    fields=line[1:].split()
                    if len(fields)<8:raise ValueError('Undated events are unsupported')
                    sec=Decimal(fields[5]);micros=sec*1000000
                    if micros!=int(micros) or not 0<=sec<60:raise ValueError('Unsupported epoch precision or leap second')
                    timestamp=(dt.datetime(*map(int,fields[:5]))-dt.datetime(1970,1,1))//dt.timedelta(microseconds=1)+int(micros)
                    flag=int(fields[6]);n=int(fields[7])
                    if flag not in (0,1):
                        if flag!=5:raise ValueError('State-changing events are unsupported')
                        for _ in range(n):
                            if not f.readline():raise ValueError('Truncated event')
                        continue
                    time_ok=q['start']<=timestamp<q['end']
                    for _ in range(n):
                        line=pending if pending is not None else f.readline();pending=None
                        sat=line[:3]
                        if len(sat)!=3 or sat[0] not in types or not sat[1:].strip().isdigit():raise ValueError('Invalid satellite record')
                        payload=line[3:].rstrip('\r\n')
                        while True:
                            nxt=f.readline()
                            if nxt.startswith('   '):payload+=nxt[3:].rstrip('\r\n')
                            else:pending=nxt;break
                        sv=ord(sat[0])*100+int(sat[1:])
                        keep=time_ok and (q['systems'] is None or sat[0] in q['systems']) and (q['satellite'] is None or q['satellite']==sv)
                        if keep:
                            if nout==cap:
                                for k in KEYS:a[k]=np.concatenate([a[k],np.empty(cap,dtype=a[k].dtype)])
                                for k,fill in [('v',np.nan),('l',-1),('s',-1)]:a[k]=np.concatenate([a[k],np.full_like(a[k],fill)])
                                cap*=2
                            for k,value in zip(KEYS,[src['id'],rid,timestamp,sv,flag]):a[k][nout]=value
                            for i,j in selected[sat[0]]:
                                cell=payload[i*16:(i+1)*16].ljust(16);number=cell[:14].strip()
                                if number:
                                    number=number.replace('D','E')
                                    a['v'][nout,j]=float(Decimal(number)) if self.decimal_values else float(number)
                                if cell[14]!=' ':a['l'][nout,j]=int(cell[14])
                                if cell[15]!=' ':a['s'][nout,j]=int(cell[15])
                                checked+=1
                            nout+=1
                        rid+=1
                parts.append({k:v[:nout] for k,v in a.items()})
        scanned=time.perf_counter()
        if parts:out={k:np.concatenate([part[k] for part in parts]) for k in KEYS+['v','l','s']}
        else:
            out={k:np.empty(0,dtype=d) for k,d in zip(KEYS,DTYPES)}
            out.update(v=np.empty((0,len(q['codes']))),l=np.empty((0,len(q['codes'])),dtype='int8'),s=np.empty((0,len(q['codes'])),dtype='int8'))
        end=time.perf_counter()
        return out,{'seconds':end-begin,'decompress_seconds':decomp,'scan_seconds':scanned-begin-decomp,'normalize_seconds':end-scanned,'checked_projected_cells':checked}
