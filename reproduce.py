"""Portable analysis and source-to-layout correctness reproduction."""
from common import *
import argparse
import copy
import shutil
import subprocess
import time

def analyze(output):
    import evidence_analysis as analysis
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    # These descriptive inventories were checked against the original files.
    # The fast path replays timings without requiring a multi-GB raw archive.
    for name in ['layout_storage.csv','dataset_roles.csv','source_inventory.csv','dataset_separation.json']:
        shutil.copyfile(ROOT/'analysis/existing_evidence'/name,output/name)
    analysis.OUT=output
    analysis.threads();candidates=analysis.candidate_sensitivity();rule=analysis.replay_predictions()
    dump(output/'reproduction.json',dict(all_passed=True,purpose='portable_timing_replay',rule=rule,
         candidate_settings=len(candidates),requires_original_data_files=False,
         outputs={p.name:sha(p) for p in sorted(output.glob('*.csv'))}))
    print('Reproduced',rule['predictions'],'predictions, including 3036 simplex scenarios.')

def source_index(root):
    result={}
    for p in Path(root).rglob('*'):
        if p.is_file() and (p.name.endswith('.rnx') or p.name.endswith('.crx.gz')):
            if p.name in result:raise ValueError('Ambiguous source filename: '+p.name)
            result[p.name]=p
    return result

def rebuilt_sources(manifest,index,output,sample,force_compressed=False):
    import hatanaka
    original=manifest['sources'][:1] if sample else manifest['sources']
    sources=[];preparation=[]
    for src in original:
        rawname=Path(src['source_file']).name;compressedname=Path(src['compressed_file']).name
        compressed=index.get(compressedname);raw=index.get(rawname)
        if compressed is not None:assert sha(compressed)==src['compressed_sha256'],compressedname
        decompress_seconds=0
        if raw is None or force_compressed:
            if compressed is None:raise FileNotFoundError(rawname+' or '+compressedname)
            raw=output/'expanded'/rawname;raw.parent.mkdir(parents=True,exist_ok=True)
            at=time.perf_counter();raw.write_bytes(hatanaka.decompress(compressed.read_bytes()))
            decompress_seconds=time.perf_counter()-at
        assert sha(raw)==src['source_sha256'],rawname
        item={k:src[k] for k in ['id','station','date','source_sha256','source_bytes','compressed_sha256','compressed_bytes','url']}
        item.update(source_file=str(raw.resolve()),compressed_file=str(compressed.resolve()) if compressed else compressedname)
        at=time.perf_counter();prepared=s.parse_source(item,output/'prepared'/src['name']);parse_seconds=time.perf_counter()-at
        assert prepared['prepared']==src['prepared'],src['name']
        sources.append(prepared)
        preparation.append(dict(source_id=src['id'],decompress_seconds=decompress_seconds,parse_seconds=parse_seconds,
                                compressed_available=compressed is not None,forced_fresh_decompression=force_compressed))
    return dict(codes=manifest['codes'],sources=sources),preparation

def rebuild(source_root,output,datasets,sample,force_compressed=False,block=0,purpose='external_functional_rebuild'):
    from layouts import ALL_METHODS,build_source,metric
    from reader import Reader,fingerprint_result,configure_pools
    configure_pools(1,4)
    output=Path(output);output.mkdir(parents=True,exist_ok=False);index=source_index(source_root)
    reports=[];construction=[];preparations={}
    for ds in datasets:
        src=ROOT/'inputs'/ds;m=load(src/'sources.json');queries=load(src/'queries.json');truth=load(src/'truth.json')
        built,preparation=rebuilt_sources(m,index,output/ds,sample,force_compressed);ids={x['id'] for x in built['sources']}
        preparations[ds]=preparation
        qs=[q for q in queries if set(q['source_ids'])<=ids]
        if sample:assert qs
        methods=ALL_METHODS if ds in ['D0m','D2m'] else CORE
        methods=sorted(methods,key=lambda name:hashlib.sha256(f'280112:construction:{ds}:{block}:{name}'.encode()).digest())
        tasks=load(src/'tasks.json') if (src/'tasks.json').exists() else []
        tasks=[q for q in tasks if set(q['source_ids'])<=ids]
        task_truth=load(src/'task_truth.json') if tasks else {}
        layouts={};metrics={}
        for method in methods:
            at=time.perf_counter()
            dest=output/ds/'layouts'/method;dest.mkdir(parents=True,exist_ok=False);entries=[];plans=[]
            for source in built['sources']:
                added,part=build_source(dest,method,source);entries.extend(added);plans.extend(part)
            dump(dest/'sources.json',built);dump(dest/'catalog.json',entries)
            dump(dest/'layout.json',dict(config=dict(name=method,family=method),files=entries))
            if plans:dump(dest/'boundary_plan.json',dict(files=plans))
            build_seconds=time.perf_counter()-at
            originals=[item for item in load(ROOT/'evidence/layout_catalogs'/ds/(method+'.json')) if item['source_id'] in ids]
            keys=['source_id','sha256','bytes','rows','row_groups']
            assert sorted(tuple(item[k] for k in keys) for item in entries)==sorted(tuple(item[k] for k in keys) for item in originals),('Rebuilt data files differ',ds,method)
            reader=Reader(dest,built,cpu=1);checked=0
            construction.append(dict(purpose=purpose,dataset=ds,block=block,method=method,sample=sample,
                decompress_seconds=sum(r['decompress_seconds'] for r in preparation),
                parse_seconds=sum(r['parse_seconds'] for r in preparation),build_seconds=build_seconds,
                init_seconds=reader.init_seconds,forced_fresh_decompression=force_compressed))
            for q in qs:
                for contract in CONTRACTS:
                    actual,_=reader.query(q,contract)
                    digest=fingerprint_result(actual,q,contract)
                    n=actual.num_rows if contract=='unordered_arrow' else len(actual['rid'])
                    assert digest==truth[q['id']]['sha256'] and n==truth[q['id']]['rows'],(ds,method,q['id'],contract)
                    del actual;checked+=1
            if tasks:
                from tasks import run_task,task_fingerprint
                for q in tasks:
                    for contract in CONTRACTS:
                        actual,_=reader.query(q,contract);product=run_task(actual,q,built,contract)
                        assert task_fingerprint(product)==task_truth[q['id']]['sha256'] and product.num_rows==task_truth[q['id']]['rows']
                        del actual,product;checked+=1
            layouts[method]=str(dest.resolve());metrics[method]=metric(dest)
            reports.append(dict(dataset=ds,method=method,checked_queries=checked,data_file_hashes_match=True,all_passed=True))
            print('Rebuilt and verified',ds,method,checked,'queries',flush=True)
        folder=output/'inputs'/ds
        for name,value in [('sources',built),('queries',qs),('truth',{q['id']:truth[q['id']] for q in qs}),('layouts',layouts),('layout_metrics',metrics)]:
            dump(folder/(name+'.json'),value)
        if tasks:
            dump(folder/'tasks.json',tasks);dump(folder/'task_truth.json',{q['id']:task_truth[q['id']] for q in tasks})
    dump(output/'verification.json',dict(all_passed=True,sample=sample,reports=reports,utc=utc()))
    dump(output/'construction_timings.json',dict(purpose=purpose,preparation=preparations,results=construction,
                                                correctness_outside_build_timing=True,original_campaign=False))

def download(destination,datasets):
    import urllib.request
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    seen=set()
    for ds in datasets:
        for source in load(ROOT/'inputs'/ds/'sources.json')['sources']:
            digest=source['compressed_sha256']
            if digest in seen:continue
            seen.add(digest);path=destination/Path(source['compressed_file']).name
            if path.exists():assert sha(path)==digest;continue
            url=source['url'];assert url.startswith('https://igs.bkg.bund.de/')
            part=path.with_suffix(path.suffix+'.part')
            with urllib.request.urlopen(url,timeout=60) as response,part.open('xb') as stream:
                shutil.copyfileobj(response,stream)
            assert sha(part)==digest;part.rename(path)
            print('Verified download',path.name,flush=True)

def benchmark(data_root,output,dataset,methods,contract,cpu,blocks,repeats):
    from reader import Reader,fingerprint_result
    from georinex_reader import GeoRinexReader
    import random,gc
    if any(m.startswith('GeoRinex') for m in methods) and contract!='ordered_dense':
        raise ValueError('GeoRinex comparisons require the ordered_dense contract')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    folder=Path(data_root)/'inputs'/dataset
    m,queries,truth,paths=[load(folder/(name+'.json')) for name in ['sources','queries','truth','layouts']]
    for block in range(blocks):
        readers={method:(GeoRinexReader(m,compressed=method.endswith('_H')) if method.startswith('GeoRinex') else Reader(Path(paths[method]),m,cpu=cpu)) for method in methods}
        rng=random.Random(280112+block);calls=0
        with (output/f'block_{block:02d}.jsonl').open('x',encoding='utf-8') as log:
            for repeat in range(-1,repeats):
                gc.collect();qs=list(queries);rng.shuffle(qs)
                for q in qs:
                    order=sorted(methods,key=lambda name:hashlib.sha256(f"280112:{block}:{repeat}:{q['id']}:{name}".encode()).digest())
                    for method in order:
                        actual,stats=readers[method].query(q,contract)
                        digest=fingerprint_result(actual,q,contract)
                        assert digest==truth[q['id']]['sha256'];del actual
                        log.write(json.dumps(dict(purpose='external_reproduction',dataset=dataset,method=method,query=q['id'],family=q['family'],
                             contract=contract,cpu=cpu,block=block,repeat=repeat,warmup=repeat<0,utc=utc(),sha256=digest,**stats))+'\n');calls+=1
        dump(output/f'block_{block:02d}.verification.json',dict(all_passed=True,calls=calls))
    dump(output/'verification.json',dict(all_passed=True,blocks=blocks,repeats=repeats,purpose='external_reproduction'))

if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('analyze');a.add_argument('--output',type=Path,required=True)
    ar=sub.add_parser('analyze-revision');ar.add_argument('--source-manifest',type=Path,default=ROOT/'evidence/revision/source_manifest.json');ar.add_argument('--output',type=Path,required=True)
    vr=sub.add_parser('verify-revision');vr.add_argument('--source-manifest',type=Path,default=ROOT/'evidence/revision/source_manifest.json');vr.add_argument('--analysis-root',type=Path,required=True)

    b=sub.add_parser('rebuild');b.add_argument('--source-root',type=Path,required=True);b.add_argument('--output',type=Path,required=True)
    b.add_argument('--datasets',nargs='+',choices=['D0m','D1','D2','D2m'],default=['D1','D2']);b.add_argument('--sample',action='store_true')
    b.add_argument('--force-compressed',action='store_true')
    c=sub.add_parser('construction');c.add_argument('--source-root',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    c.add_argument('--datasets',nargs='+',choices=['D0m','D1','D2','D2m'],default=['D0m','D1','D2','D2m'])
    c.add_argument('--blocks',type=int,default=6);c.add_argument('--sample',action='store_true')
    d=sub.add_parser('download');d.add_argument('--output',type=Path,required=True);d.add_argument('--datasets',nargs='+',choices=['D0m','D1','D2','D2m'],default=['D0m','D1','D2'])
    t=sub.add_parser('benchmark');t.add_argument('--data-root',type=Path,required=True);t.add_argument('--output',type=Path,required=True)
    t.add_argument('--dataset',choices=['D0m','D1','D2','D2m'],default='D2');t.add_argument('--methods',nargs='+',default=CORE)
    t.add_argument('--contract',choices=CONTRACTS,default='ordered_dense');t.add_argument('--cpu',type=int,choices=[1,4,8,16],default=1)
    t.add_argument('--blocks',type=int,default=6);t.add_argument('--repeats',type=int,default=3)
    args=p.parse_args()
    if args.command=='analyze-revision':
        from revision_analyze import analyze as run_revision
        run_revision(args.source_manifest,args.output)
    elif args.command=='verify-revision':
        from revision_verify import verify
        verify(args.source_manifest,args.analysis_root)
    elif args.command=='analyze':analyze(args.output)
    elif args.command=='rebuild':rebuild(args.source_root,args.output,args.datasets,args.sample,args.force_compressed)
    elif args.command=='construction':
        args.output.mkdir(parents=True,exist_ok=False)
        for block in range(args.blocks):
            rebuild(args.source_root,args.output/f'block_{block:02d}',args.datasets,args.sample,True,block,'external_construction_reproduction')
    elif args.command=='download':download(args.output,args.datasets)
    else:benchmark(args.data_root,args.output,args.dataset,args.methods,args.contract,args.cpu,args.blocks,args.repeats)
