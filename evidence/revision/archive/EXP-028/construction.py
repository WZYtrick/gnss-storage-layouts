"""Fresh six-block ingestion measurements; existing Parquet files are never copied."""
from common import *
import argparse
import copy
import gc
import random
import time
import traceback
import hatanaka
from layouts import ALL_METHODS, build_source, metric
from reader import Reader,configure_pools

def measure(dataset,block,folder,smoke=False):
    configure_pools(1,4)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    m,qs,truth,oldpaths=inputs(dataset)
    original=copy.deepcopy(m)
    if smoke:
        original['sources']=[max(m['sources'],key=lambda x:x['declared_cells'])]
    methods=ALL_METHODS if dataset in ('D0m','D2m') else CORE
    order=sorted(methods,key=lambda x:hashlib.sha256(f'280112:construction:{dataset}:{block}:{x}'.encode()).digest())
    started=time.perf_counter();decomp=0.;parse=0.;sources=[];preparation=[]
    dump(folder/'status.json',dict(state='running',phase='preparation',utc=utc(),pid=os.getpid()))
    for source in original['sources']:
        if not smoke and not in_window():raise RuntimeError('Measurement window ended during incomplete construction block')
        name=source['name'];out=folder/'expanded'/Path(source['source_file']).name
        out.parent.mkdir(parents=True,exist_ok=True)
        at=time.perf_counter()
        payload=hatanaka.decompress(Path(source['compressed_file']).read_bytes())
        out.write_bytes(payload);del payload
        elapsed=time.perf_counter()-at;decomp+=elapsed
        assert sha(out)==source['source_sha256']
        item={k:source[k] for k in ['id','station','date','source_sha256','source_bytes','compressed_file','compressed_sha256','compressed_bytes','url']}
        item['source_file']=str(out.resolve())
        at=time.perf_counter();prepared=s.parse_source(item,folder/'prepared'/name);parse_elapsed=time.perf_counter()-at;parse+=parse_elapsed
        assert prepared['prepared']==source['prepared'],('Prepared arrays changed',dataset,name)
        sources.append(prepared)
        preparation.append(dict(source_id=source['id'],source=name,decompress_seconds=elapsed,parse_seconds=parse_elapsed,
                                arrays_match_original=True,source_sha256=source['source_sha256']))
        dump(folder/'status.json',dict(state='running',phase='preparation',sources_done=len(sources),sources_total=len(original['sources']),utc=utc(),pid=os.getpid()))
    prepared_manifest=dict(codes=m['codes'],sources=sources,split=dataset)
    dump(folder/'preparation.json',dict(dataset=dataset,block=block,decompress_seconds=decomp,parse_seconds=parse,sources=preparation))
    results=[]
    for method in order:
        if not smoke and not in_window():raise RuntimeError('Measurement window ended during incomplete construction block')
        dest=folder/'layouts'/method
        at=time.perf_counter();dest.mkdir(parents=True,exist_ok=False);entries=[];plans=[]
        for source in sources:
            added,source_plans=build_source(dest,method,source);entries.extend(added);plans.extend(source_plans)
        s.dump(dest/'sources.json',prepared_manifest)
        s.dump(dest/'layout.json',dict(config=dict(name=method,family=method),files=entries))
        s.dump(dest/'catalog.json',entries)
        if plans:s.dump(dest/'boundary_plan.json',dict(files=plans))
        build_seconds=time.perf_counter()-at
        size=metric(dest)
        reader=Reader(dest,prepared_manifest,cpu=1)
        init_seconds=reader.init_seconds
        # Full-source validation is deliberately outside construction/init time.
        checked=0;full_hashes=[]
        for source in sources:
            q=dict(source_ids=[source['id']],start=s.epoch_base(source),end=s.epoch_base(source)+86400000000,
                   codes=m['codes'],systems=None,satellite=None)
            actual,_=reader.query(q,'ordered_dense')
            expected=s.reference(prepared_manifest,q)
            a=s.fingerprint(actual);b=s.fingerprint(expected)
            assert a==b and len(actual['rid'])==source['records'],(dataset,method,source['name'])
            checked+=len(actual['rid']);full_hashes.append(dict(source_id=source['id'],rows=len(actual['rid']),sha256=a))
            del actual,expected
        if not smoke:
            old=load(ROOT/'inputs'/dataset/'layout_metrics.json')[method]
            assert size['rows']==old['rows'] and size['files']==old['files'] and size['row_groups']==old['row_groups']
            # Encoding, row grouping and numeric content are also checked by
            # comparing the multiset of original and freshly built file hashes.
        source_ids={x['id'] for x in sources}
        previous=[x for x in load(Path(oldpaths[method])/'catalog.json') if x['source_id'] in source_ids]
        assert sorted((x['source_id'],x['sha256']) for x in entries)==sorted((x['source_id'],x['sha256']) for x in previous)
        record=dict(dataset=dataset,block=block,method=method,decompress_seconds=decomp,parse_seconds=parse,
                    build_seconds=build_seconds,init_seconds=init_seconds,
                    from_rnx_seconds=parse+build_seconds+init_seconds,
                    from_crx_seconds=decomp+parse+build_seconds+init_seconds,
                    verified_rows=checked,full_source_hashes=full_hashes,**size)
        results.append(record);dump(folder/'results.partial.json',results)
        dump(folder/'status.json',dict(state='running',phase='layouts',method=method,done=len(results),total=len(order),utc=utc(),pid=os.getpid()))
        del reader;gc.collect()
        print('BUILD',dataset,block,method,round(build_seconds,3),'s',size['files'],'files OK',flush=True)
    if not smoke and not in_window():raise RuntimeError('Measurement window ended before block completion')
    report=dict(all_passed=True,purpose='smoke' if smoke else 'formal',dataset=dataset,block=block,
                methods=order,sources=len(sources),elapsed_seconds=time.perf_counter()-started,results=results,
                code={name:sha(ROOT/name) for name in ['common.py','storage.py','reader.py','layouts.py','construction.py']},
                ended_utc=utc(),time_policy='smoke_anytime' if smoke else 'Beijing_01_08')
    dump(folder/'verification.json',report);dump(folder/'status.json',dict(state='complete',utc=utc(),pid=os.getpid()))
    if smoke:dump(ROOT/'smoke'/('construction_'+dataset+'_latest.json'),entry(folder/'verification.json'))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',choices=['D0m','D1','D2','D2m'],required=True)
    p.add_argument('--block',type=int,default=0);p.add_argument('--output',type=Path,required=True);p.add_argument('--smoke',action='store_true')
    a=p.parse_args()
    try:measure(a.dataset,a.block,a.output,a.smoke)
    except BaseException:
        dump(a.output/'failure.json',dict(utc=utc(),traceback=traceback.format_exc()));raise
