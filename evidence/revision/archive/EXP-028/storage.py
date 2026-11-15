"""RINEX 3 raw-label observation store; all methods share semantics and output."""
from pathlib import Path
from collections import Counter
from decimal import Decimal
import datetime as dt
import hashlib, itertools, json, platform, re, sys, time
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as fs
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent
KEYS=['source_id','rid','t','sv','epoch_flag']
DTYPES=['uint32','uint32','int64','uint16','int8']
pa.set_cpu_count(1);pa.set_io_thread_count(1)
def dump(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def load(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()
def source_date(src):
    """Resolve a complete calendar date; never infer an unrecorded year."""
    if isinstance(src,dt.datetime): return src.date()
    if isinstance(src,dt.date): return src
    if isinstance(src,str): return dt.date.fromisoformat(src)
    if not isinstance(src,dict): raise ValueError('A complete date or source record is required')
    if src.get('date'): return dt.date.fromisoformat(src['date'])
    if 'year' in src and 'day' in src:
        date=dt.date(int(src['year']),1,1)+dt.timedelta(days=int(src['day'])-1)
        if date.year!=int(src['year']): raise ValueError('Day of year exceeds recorded year')
        return date
    for line in src.get('header',[]):
        if line[60:].strip()=='TIME OF FIRST OBS':
            return dt.date(*map(int,line[:18].split()))
    raise ValueError('Source has no complete calendar date')
def epoch_base(src):
    return (dt.datetime.combine(source_date(src),dt.time())-dt.datetime(1970,1,1))//dt.timedelta(microseconds=1)
def source_time_system(src):
    for line in src.get('header',[]):
        if line[60:].strip()=='TIME OF FIRST OBS':
            system=line[48:51].strip()
            if system:
                if system not in {'GPS','GLO','GAL','QZS','BDT','IRN'}: raise ValueError('Unsupported time system '+system)
                return system
    systems=list(src.get('schema',{}))
    if len(systems)==1:
        return {'G':'GPS','R':'GLO','E':'GAL','J':'QZS','C':'BDT','I':'IRN','S':'GPS'}[systems[0]]
    raise ValueError('Mixed-system source must declare TIME OF FIRST OBS time system')
def header(lines):
    fields={};counts={};current=None
    for i,l in enumerate(lines):
        label=l[60:].strip()
        if label=='SYS / # / OBS TYPES':
            if l[0]!=' ': current=l[0];counts[current]=int(l[3:6]);fields[current]=[]
            fields[current].extend(l[7:60].split())
        if label=='END OF HEADER': break
    assert fields and all(len(fields[s])==counts[s] for s in fields)
    return i+1,fields
def parse_source(item,out):
    started=time.perf_counter();p=Path(item['source_file']);source_hash=sha(p)
    assert source_hash==item['source_sha256']
    lines=p.read_text(encoding='ascii').splitlines();i,types=header(lines);headers=lines[:i]
    assert float(headers[0][:9])>=3 and float(headers[0][:9])<4
    date=source_date({**item,'header':headers})
    time_system=source_time_system({'header':headers,'schema':types})
    codes=sorted({c for cc in types.values() for c in cc});ci={c:j for j,c in enumerate(codes)}
    assert all(re.fullmatch('[CLDS][0-9][A-Z]',c) for c in codes)
    size=262144;v=np.full((size,len(codes)),np.nan);l=np.full(v.shape,-1,dtype='int8');s=l.copy()
    t=np.empty(size,dtype='int64');sv=np.empty(size,dtype='uint16');ef=np.empty(size,dtype='int8')
    nr=0;declared=0;sample_checked=0;events=[];epochs=[]
    while i<len(lines):
        assert lines[i].startswith('>'),(p.name,i,lines[i][:40])
        line=lines[i];e=line.split();assert len(e)>=9
        calendar=dt.datetime(*map(int,e[1:6]))
        seconds=Decimal(e[6]);micros=int(seconds*1000000)
        if Decimal(micros)!=seconds*1000000: raise ValueError('Sub-microsecond epochs are unsupported')
        if not 0<=seconds<60: raise ValueError('Leap-second labels are unsupported')
        timestamp=(calendar-dt.datetime(1970,1,1))//dt.timedelta(microseconds=1)+micros
        flag=int(e[7]);n=int(e[8]);i+=1
        if flag not in [0,1]:
            extra=lines[i:i+n];assert len(extra)==n
            if flag!=5: raise ValueError('Only static headers and external event flag 5 are supported; state-changing event '+str(flag))
            events.append({'t':timestamp,'flag':flag,'line':line,'records':extra});i+=n;continue
        epochs.append({'t':timestamp,'line':line})
        if not epoch_base(date)<=timestamp<epoch_base(date)+86400000000: raise ValueError('Observation is outside declared source date')
        for j in range(n):
            first=lines[i];assert re.match(r'^[A-Z][ 0-9][0-9]',first),(p.name,i,first[:40])
            satellite=first[:3];payload=first[3:];i+=1
            while i<len(lines) and lines[i].startswith('   '): payload+=lines[i][3:];i+=1
            fields=types[satellite[0]]
            assert not payload[16*len(fields):].strip()
            payload=payload[:16*len(fields)].ljust(16*len(fields))
            if nr==size:
                size*=2
                v=np.concatenate([v,np.full_like(v,np.nan)]);l=np.concatenate([l,np.full_like(l,-1)]);s=np.concatenate([s,np.full_like(s,-1)])
                t=np.resize(t,size);sv=np.resize(sv,size);ef=np.resize(ef,size)
            for k,c in enumerate(fields):
                cell=payload[k*16:(k+1)*16];a=cell[:14].strip();col=ci[c]
                if a:
                    v[nr,col]=float(a.replace('D','E'));assert np.isfinite(v[nr,col])
                if cell[14]!=' ': l[nr,col]=int(cell[14])
                if cell[15]!=' ': s[nr,col]=int(cell[15])
                if nr%100==0:
                    assert (not a and np.isnan(v[nr,col])) or (bool(a) and float(Decimal(a.replace('D','E')))==v[nr,col])
                    assert l[nr,col]==(-1 if cell[14]==' ' else int(cell[14]))
                    assert s[nr,col]==(-1 if cell[15]==' ' else int(cell[15]))
                    sample_checked+=1
            declared+=len(fields);t[nr]=timestamp;sv[nr]=ord(satellite[0])*100+int(satellite[1:]);ef[nr]=flag;nr+=1
    out.mkdir(exist_ok=False,parents=True)
    data={'v':v[:nr],'l':l[:nr],'s':s[:nr],'t':t[:nr],'sv':sv[:nr],'epoch_flag':ef[:nr]}
    for k,a in data.items(): np.save(out/(k+'.npy'),a)
    numeric=int(np.isfinite(data['v']).sum());active=np.isfinite(data['v'])|(data['l']>=0)|(data['s']>=0)
    result={**item,'name':out.name,'date':date.isoformat(),'year':date.year,'day':date.timetuple().tm_yday,'time_system':time_system,'prepared_dir':str(out.resolve()),'codes':codes,'schema':types,'header':headers,'events':events,'epoch_lines':epochs,
        'records':nr,'systems':dict(Counter(chr(int(a)//100) for a in data['sv'])),
        'declared_cells':declared,'numeric_cells':numeric,'nonblank_cells':int(active.sum()),'all_empty_rows':int((~active.any(axis=1)).sum()),
        'duplicate_epoch_sat_rows':nr-len(set(zip(map(int,data['t']),map(int,data['sv'])))),
        'decimal_sample_checks':sample_checked,'parse_seconds':time.perf_counter()-started,
        'prepared':{k:sha(out/(k+'.npy')) for k in data},'time_semantics':'microseconds since 1970 in source calendar labels, NOT converted UTC'}
    dump(out/'source.json',result);assert sha(p)==source_hash
    print('prepared',out.name,nr,'rows',round(result['parse_seconds'],2),'s',flush=True)
    return result
def prepare_sources(items,output_root):
    records=[]
    for item in items:
        date=source_date(item);name=f"{item['station']}_{date:%Y%m%d}"
        records.append(parse_source(item,Path(output_root)/name))
    return {'codes':sorted({c for a in records for c in a['codes']}),'sources':records}
def arrays(s):
    directory=Path(s['prepared_dir'])
    return {k:np.load(directory/(k+'.npy'),mmap_mode='r') for k in ['v','l','s','t','sv','epoch_flag']}
def schema(codes):
    fields=[(k,pa.from_numpy_dtype(np.dtype(d))) for k,d in zip(KEYS,DTYPES)]
    for c in codes: fields.extend([(f'v_{c}',pa.float64()),(f'l_{c}',pa.int8()),(f's_{c}',pa.int8())])
    return pa.schema(fields)
def table_for(s,d,idx,codes):
    data={'source_id':pa.array(np.full(len(idx),s['id'],dtype='uint32')),'rid':pa.array(idx.astype('uint32'))}
    data.update({k:pa.array(d[k][idx]) for k in ['t','sv','epoch_flag']})
    for c in codes:
        j=s['codes'].index(c)
        data['v_'+c]=pa.array(d['v'][idx,j],mask=np.isnan(d['v'][idx,j]),type=pa.float64())
        for k in ['l','s']: data[k+'_'+c]=pa.array(d[k][idx,j],mask=d[k][idx,j]<0,type=pa.int8())
    return pa.table(data)
def sort_table(table,order):
    keys=['t','sv','rid'] if order=='time' else ['sv','t','rid']
    return table.take(pc.sort_indices(table,sort_keys=[(k,'ascending') for k in keys]))
def writer(p,sch):
    floats=[f.name for f in sch if pa.types.is_floating(f.type)];other=[f.name for f in sch if f.name not in floats]
    return pq.ParquetWriter(p,sch,compression='zstd',compression_level=3,use_dictionary=other,use_byte_stream_split=floats,write_statistics=True,version='2.6')
def configurations():
    out=[{'name':f'{f}_{o}_{r}','family':f,'order':o,'rowgroup':r} for f,o,r in itertools.product(['W','C'],['time','entity'],[1024,8192,65536])]
    out += [{'name':f'{f}_{m}m','family':f,'order':'entity','minutes':m} for f,m in itertools.product(['T','F'],[30,60,120])]
    return out
def build_source(dest,c,s):
    d=arrays(s);entries=[];parts=['all'] if c['family']=='W' else sorted(s['systems'])
    for part in parts:
        idx=np.arange(len(d['t']))
        if part!='all': idx=idx[d['sv']//100==ord(part)]
        codes=s['codes'] if part=='all' else sorted(s['schema'][part])
        tab=table_for(s,d,idx,codes);base=f"{s['name']}_{part}"
        def register(p):
            md=pq.read_metadata(p);rec={'file':p.name,'source_id':s['id'],'bytes':p.stat().st_size,'sha256':sha(p),'rows':md.num_rows,'row_groups':md.num_row_groups,'codes':codes}
            for field,col in [('source_id',0),('t',2),('sv',3)]:
                stats=[md.row_group(i).column(col).statistics for i in range(md.num_row_groups)]
                assert all(a is not None and a.has_min_max for a in stats)
                lo=int(min(a.min for a in stats));hi=int(max(a.max for a in stats))
                if field=='source_id': assert lo==hi==s['id']
                else:rec[field+'_min']=lo;rec[field+'_max']=hi
            entries.append(rec)
        if c['family'] in ['W','C']:
            p=dest/(base+'.parquet')
            with writer(p,tab.schema) as w: w.write_table(sort_table(tab,c['order']),row_group_size=c['rowgroup'])
            register(p)
        else:
            width=c['minutes']*60000000;blocks=[];day=epoch_base(s)
            for start in range(day,day+86400000000,width):
                block=tab.filter(pc.and_(pc.greater_equal(tab['t'],start),pc.less(tab['t'],start+width)))
                if len(block):blocks.append((start,sort_table(block,'entity')))
            if c['family']=='T':
                p=dest/(base+'.parquet')
                with writer(p,tab.schema) as w:
                    for _,block in blocks:w.write_table(block,row_group_size=len(block))
                register(p)
            else:
                for start,block in blocks:
                    p=dest/f'{base}_{(start-day)//1000000:05d}.parquet'
                    with writer(p,tab.schema) as w:w.write_table(block,row_group_size=len(block))
                    register(p)
    return entries
def build_config(target,c,m):
    dest=target/c['name'];dest.mkdir(parents=True,exist_ok=False);start=time.perf_counter();entries=[];costs=[]
    for s in m['sources']:
        begin=time.perf_counter();entries.extend(build_source(dest,c,s));costs.append({'source':s['name'],'seconds':time.perf_counter()-begin})
    dump(dest/'sources.json',m);dump(dest/'layout.json',{'config':c,'files':entries});dump(dest/'catalog.json',entries)
    result={**c,'build_seconds':time.perf_counter()-start,'source_costs':costs,'files':len(entries),'parquet_bytes':sum(e['bytes'] for e in entries),'total_bytes':sum(p.stat().st_size for p in dest.iterdir()),'row_groups':sum(e['row_groups'] for e in entries),'physical_rows':sum(e['rows'] for e in entries)}
    print('built',c['name'],result['files'],round(result['build_seconds'],2),'s',flush=True)
    return result
def sources_for(m,q): return [s for s in m['sources'] if s['id'] in q['source_ids']]
def reference(m,q):
    items=[]
    for s in sorted(sources_for(m,q),key=lambda s:s['id']):
        d=arrays(s);mask=(d['t']>=q['start'])&(d['t']<q['end'])
        if q['systems'] is not None:mask &= np.isin(d['sv']//100,[ord(a) for a in q['systems']])
        if q['satellite'] is not None:mask &= d['sv']==q['satellite']
        idx=np.flatnonzero(mask);row={'source_id':np.full(len(idx),s['id'],dtype='uint32'),'rid':idx.astype('uint32')}
        row.update({k:d[k][idx] for k in ['t','sv','epoch_flag']})
        for k,dtype,fill in [('v','float64',np.nan),('l','int8',-1),('s','int8',-1)]:
            a=np.full((len(idx),len(q['codes'])),fill,dtype=dtype)
            for j,c in enumerate(q['codes']):
                if c in s['codes']:a[:,j]=d[k][idx,s['codes'].index(c)]
            row[k]=a
        items.append(row)
    if not items:return empty(q['codes'])
    return {k:np.concatenate([a[k] for a in items],axis=0) for k in KEYS+['v','l','s']}
def empty(codes):
    out={k:np.empty(0,dtype=d) for k,d in zip(KEYS,DTYPES)}
    out.update(v=np.empty((0,len(codes)),dtype='float64'),l=np.empty((0,len(codes)),dtype='int8'),s=np.empty((0,len(codes)),dtype='int8'))
    return out
def fingerprint(a):
    h=hashlib.sha256()
    for k in KEYS+['v','l','s']:
        x=np.ascontiguousarray(a[k]);h.update(k.encode());h.update(str(x.shape).encode());h.update(x.dtype.str.encode());h.update(x.tobytes())
    return h.hexdigest()
def normalize(tab,codes):
    tab=tab.take(pc.sort_indices(tab,sort_keys=[('source_id','ascending'),('rid','ascending')]))
    out={k:tab[k].to_numpy().astype(d,copy=False) for k,d in zip(KEYS,DTYPES)}
    for k,dtype,fill in [('v','float64',np.nan),('l','int8',-1),('s','int8',-1)]:
        out[k]=np.column_stack([pc.fill_null(tab[k+'_'+c],fill).to_numpy().astype(dtype,copy=False) for c in codes])
    return out
class Reader:
    def __init__(self,directory,m):
        start=time.perf_counter();self.manifest=m;self.directory=directory;self.catalog=load(directory/'catalog.json')
        filesystem=fs.LocalFileSystem();fmt=ds.ParquetFileFormat();fragments=[]
        for e in self.catalog:
            expr=ds.field('source_id')==e['source_id']
            for k in ['t','sv']:expr=expr&(ds.field(k)>=e[k+'_min'])&(ds.field(k)<=e[k+'_max'])
            f=fmt.make_fragment(str(directory/e['file']),filesystem=filesystem,partition_expression=expr,file_size=e['bytes']);f.ensure_complete_metadata();fragments.append(f)
        self.dataset=ds.FileSystemDataset(fragments,schema(m['codes']),fmt,filesystem);self.init_seconds=time.perf_counter()-start
    def query(self,q):
        start=time.perf_counter();expr=ds.field('source_id').isin(q['source_ids'])&(ds.field('t')>=q['start'])&(ds.field('t')<q['end'])
        if q['systems'] is not None:
            ss=None
            for system in q['systems']:
                low=ord(system)*100;item=(ds.field('sv')>=low)&(ds.field('sv')<low+100);ss=item if ss is None else ss|item
            expr=expr&ss
        if q['satellite'] is not None:expr=expr&(ds.field('sv')==q['satellite'])
        cols=KEYS+[f'{k}_{c}' for c in q['codes'] for k in ['v','l','s']]
        tab=self.dataset.scanner(columns=cols,filter=expr,use_threads=False,batch_readahead=1,fragment_readahead=1).to_table();scanned=time.perf_counter()
        out=normalize(tab,q['codes']);end=time.perf_counter()
        return out,{'scan_seconds':scanned-start,'normalize_seconds':end-scanned,'seconds':end-start}
def environment():
    import psutil
    return {'python':sys.version,'numpy':np.__version__,'pyarrow':pa.__version__,'platform':platform.platform(),'cpu':platform.processor(),'logical_cpus':psutil.cpu_count(),'memory_bytes':psutil.virtual_memory().total,'single_thread':True,'os_file_cache':'warm','protocol_sha256':sha(ROOT/'protocol.md'),'storage_script_sha256':sha(Path(__file__))}
if __name__=='__main__':
    raise SystemExit('Use reproduce.py or import prepare_sources with explicit source dates')
