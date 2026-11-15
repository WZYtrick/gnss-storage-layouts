"""Frozen revision campaign with whole-block checkpoints and 01:00-08:00 gating."""
from common import *
import argparse
import gc
import importlib.metadata as metadata
import platform
import random
import statistics
import subprocess
import time
import traceback
import psutil

RUNTIME=['common.py','storage.py','reader.py','layouts.py','georinex_reader.py','construction.py','campaign.py']
PINNED={'numpy':'2.3.5','pyarrow':'25.0.1','georinex':'1.16.2','xarray':'2026.9.0','hatanaka':'2.8.1'}

def runtime_hashes():return {name:sha(ROOT/name) for name in RUNTIME}

def guard(design):
    assert sys.version.startswith('3.12.14'),sys.version
    assert {name:metadata.version(name) for name in PINNED}==PINNED
    assert runtime_hashes()==design['runtime_code'], 'Frozen measurement code changed'
    for item in design['input_files']:
        p=ROOT/item['path']
        assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],str(p)

def verify_assets(dataset):
    manifest,_,_,paths=inputs(dataset)
    metrics=load(ROOT/'inputs'/dataset/'layout_metrics.json')
    for source in manifest['sources']:
        for kind in ['source','compressed']:
            assert sha(source[kind+'_file'])==source[kind+'_sha256'],(dataset,source['id'],kind)
    for method,directory in paths.items():
        directory=Path(directory)
        assert sha(directory/'catalog.json')==metrics[method]['catalog_sha256']
        for item in load(directory/'catalog.json'):
            p=directory/item['file']
            assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],str(p)

def validation_budget():
    latest=load(ROOT/'validation/all_latest.json');folder=Path(latest['directory'])
    cert=load(folder/'verification.json');assert cert['all_passed'] and cert['queries']==864
    assert latest['code']=={name:sha(ROOT/name) for name in latest['code']},'Baseline validation is stale'
    records=[]
    for p in folder.glob('*.json'):
        if p.name in ['failure.json','verification.json']:continue
        r=load(p);assert r['all_passed'];records.append(r)
    assert len(records)==864
    budgets={}
    for dataset in ['D1','D2']:
        m,qs,truth,_=inputs(dataset)
        group=[r for r in records if r['dataset']==dataset]
        assert {(r['method'],r['query']) for r in group}=={(method,q['id']) for method in METHODS[-2:] for q in qs}
        raw_seconds=sum(r['seconds'] for r in group)
        # The old CPU=1 core times add only a small fraction; 120 seconds also
        # covers validation/logging/init. Preserve a 15% scheduling margin.
        budgets[dataset]=dict(validation_query_seconds=raw_seconds,estimated_block_seconds=raw_seconds*4+240,
                              scheduling_seconds=(raw_seconds*4+240)*1.15)
    return latest,cert,budgets

def construction_smoke(dataset):
    receipt=load(ROOT/'smoke'/('construction_'+dataset+'_latest.json'))
    assert sha(receipt['path'])==receipt['sha256']
    report=load(receipt['path']);assert report['all_passed']
    assert report['code']=={name:sha(ROOT/name) for name in report['code']}
    return report

def freeze(run_id):
    root=ROOT/'campaigns'/run_id
    assert not root.exists(),root
    latest,cert,budgets=validation_budget()
    regression=load(ROOT/'validation/regression_tests.json')
    assert regression['all_passed'] and regression['runtime_code']==runtime_hashes(),'Rerun current-code regression checks'
    assert load(ROOT/'validation/analysis_regression_tests.json')['all_passed']
    assert load(ROOT/'validation/independent_existing_evidence.json')['all_passed']
    files=[dict(path='protocol.md',bytes=(ROOT/'protocol.md').stat().st_size,sha256=sha(ROOT/'protocol.md'))];source_checks=[]
    for ds in ['D0m','D1','D2','D2m']:
        for name in ['sources','queries','truth','layouts','layout_metrics']:
            p=ROOT/'inputs'/ds/(name+'.json');files.append(dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,sha256=sha(p)))
        m,_,_,paths=inputs(ds)
        for src in m['sources']:
            for field in ['source','compressed']:
                path=Path(src[field+'_file']);assert sha(path)==src[field+'_sha256']
                source_checks.append(dict(dataset=ds,source_id=src['id'],kind=field,sha256=src[field+'_sha256'],bytes=path.stat().st_size))
        smoke=construction_smoke(ds)
        # Every formal layout is checked against its original catalog on read.
        for method,path in paths.items():
            for item in load(Path(path)/'catalog.json'):
                p=Path(path)/item['file'];assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256']
    jobs=[]
    for block in range(6):
        for ds in (['D1','D2'] if block%2==0 else ['D2','D1']):
            jobs.append(dict(id=f'baseline_{ds}_b{block:02d}',kind='baseline',dataset=ds,block=block,
                             scheduling_seconds=budgets[ds]['scheduling_seconds']))
            # Fill the remainder after a long baseline block with short fresh
            # construction blocks. This ordering is fixed before measurement.
            pair=[ds,'D0m' if ds=='D1' else 'D2m']
            for build_ds in sorted(pair,key=lambda name:hashlib.sha256(f'280112:build:{block}:{name}'.encode()).digest()):
                smoke=construction_smoke(build_ds);ns=len(inputs(build_ds)[0]['sources'])
                jobs.append(dict(id=f'build_{build_ds}_b{block:02d}',kind='construction',dataset=build_ds,block=block,
                                 scheduling_seconds=smoke['elapsed_seconds']*ns*1.3+120))
    excessive=[j for j in jobs if j['scheduling_seconds']>7*3600]
    if excessive:
        dump(ROOT/'validation/window_budget_conflict.json',dict(jobs=excessive,budgets=budgets))
        raise RuntimeError('A complete block exceeds the authorized seven-hour window; protocol decision required')
    root.mkdir(parents=True)
    design=dict(run_id=run_id,created=utc(),purpose='post-review supplementary measurement',
                time_policy=dict(timezone='Asia/Shanghai',start='01:00',end='08:00',authorization_date='2026-11-12'),
                blocks=6,repeats=3,warmups=1,methods=METHODS,contract='ordered_dense',cpu=1,io=4,
                expected=dict(formal_query_calls=54432,warmup_query_calls=18144,layout_builds=204),
                runtime_code=runtime_hashes(),input_files=files,source_checks=source_checks,versions=PINNED,
                analysis_code={name:sha(ROOT/name) for name in ['analyze_revision.py','independent_revision.py']},
                baseline_validation=entry(Path(latest['directory'])/'verification.json'),
                budget=budgets,jobs=jobs,baseline_hard_timeout_seconds=7*3600,
                old_D2_predictions_sha256=sha(ROOT/'evidence/freezes/D2_predictions.json'))
    dump(root/'design.json',design)
    dump(root/'status.json',dict(state='frozen',completed=[],utc=utc(),estimated_active_seconds=sum(j['scheduling_seconds'] for j in jobs)))
    return design

def baseline(design,job,out):
    from reader import Reader,configure_pools
    from georinex_reader import GeoRinexReader
    import pyarrow as pa
    out.mkdir(parents=True,exist_ok=False)
    m,qs,truth,paths=inputs(job['dataset']);configure_pools(1,4)
    readers={method:Reader(Path(paths[method]),m,cpu=1) for method in CORE}
    readers.update(GeoRinex_R=GeoRinexReader(m),GeoRinex_H=GeoRinexReader(m,compressed=True))
    process=psutil.Process();identity=dict(pid=process.pid,created=process.create_time(),executable=process.exe())
    dump(out/'environment.json',dict(python=sys.version,versions=PINNED,platform=platform.platform(),
                                    process=identity,cpu_pool=pa.cpu_count(),io_pool=pa.io_thread_count(),
                                    memory_bytes=psutil.virtual_memory().total,thread_env={k:os.environ.get(k) for k in ENV_THREADS},
                                    reader_init_seconds={name:reader.init_seconds for name,reader in readers.items()}))
    dump(out/'job.json',job);nformal=nwarm=0;order=0;started=time.perf_counter()
    rng=random.Random(280112+job['block'])
    with (out/'timings.jsonl').open('x',encoding='utf-8') as f:
        for repeat in [-1,0,1,2]:
            gc.collect();requests=list(qs);rng.shuffle(requests)
            for q in requests:
                methods=sorted(METHODS,key=lambda method:hashlib.sha256(f"280112:{job['block']}:{repeat}:{q['id']}:{method}".encode()).digest())
                for method in methods:
                    if not in_window():raise RuntimeError('Authorized time window ended before call')
                    begin=utc();wall=time.perf_counter()
                    actual,stats=readers[method].query(q,'ordered_dense')
                    end=utc()
                    rows=len(actual['rid']);digest=s.fingerprint(actual);del actual
                    assert rows==truth[q['id']]['rows'] and digest==truth[q['id']]['sha256'],(job['dataset'],method,q['id'])
                    if not in_window():raise RuntimeError('Authorized time window ended during call; block is incomplete')
                    record=dict(dataset=job['dataset'],block=job['block'],query=q['id'],family=q['family'],method=method,
                                repeat=repeat,warmup=repeat<0,contract='ordered_dense',cpu=1,io=4,rows=rows,sha256=digest,
                                started_utc=begin,ended_utc=end,time_policy='Beijing_01_08',call_order=order,
                                process=identity,call_with_validation_seconds=time.perf_counter()-wall,**stats)
                    f.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n');f.flush()
                    order+=1;nwarm+=repeat<0;nformal+=repeat>=0
                    dump(out/'progress.json',dict(utc=utc(),pid=os.getpid(),formal=nformal,warmup=nwarm,total=6048,
                                                 query=q['id'],method=method,repeat=repeat,elapsed_seconds=time.perf_counter()-started))
                print(job['id'],'pass',repeat,q['id'],'calls',order,flush=True)
    assert nformal==4536 and nwarm==1512
    guard(design)
    dump(out/'verification.json',dict(all_passed=True,formal_calls=nformal,warmup_calls=nwarm,
                                     elapsed_seconds=time.perf_counter()-started,code=runtime_hashes(),ended_utc=utc()))

def worker(root,job_id):
    design=load(root/'design.json');guard(design);job=next(j for j in design['jobs'] if j['id']==job_id)
    if not in_window():raise RuntimeError('Formal worker was started outside 01:00-08:00 Beijing')
    out=root/'runs'/job_id
    try:
        verify_assets(job['dataset'])
        if job['kind']=='baseline':baseline(design,job,out)
        else:
            from construction import measure
            measure(job['dataset'],job['block'],out,smoke=False)
            guard(design)
        verify_assets(job['dataset'])
    except BaseException:
        dump(out/'failure.json',dict(utc=utc(),traceback=traceback.format_exc()));raise

def run(run_id,wait_validation=False):
    root=ROOT/'campaigns'/run_id
    ROOT.joinpath('campaigns').mkdir(exist_ok=True)
    # Runtime request prevents automatic idle sleep without changing power plans.
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    lock=ROOT/'campaigns'/('supervisor_'+run_id+'.json')
    identity=dict(pid=os.getpid(),created=psutil.Process().create_time(),utc=utc(),run_id=run_id)
    if lock.exists():
        old=load(lock)
        try:
            if psutil.Process(old['pid']).create_time()==old['created']:raise RuntimeError('Supervisor already running')
        except psutil.NoSuchProcess:pass
    dump(lock,identity)
    try:
        while not (root/'design.json').exists():
            if (ROOT/'validation/all_latest.json').exists():
                freeze(run_id);break
            if not wait_validation:raise RuntimeError('Full baseline validation is not complete')
            dump(ROOT/'campaigns'/('pending_'+run_id+'.json'),dict(**identity,state='waiting_for_complete_validation',updated_utc=utc()))
            if (ROOT/'STOP_AFTER_BLOCK').exists():return
            time.sleep(30)
        design=load(root/'design.json');guard(design)
        for job in design['jobs']:
            cert=root/'runs'/job['id']/'verification.json'
            if (cert.parent/'failure.json').exists():raise RuntimeError('Failed block retained; review required: '+str(cert.parent))
            if cert.exists():
                assert load(cert)['all_passed'];continue
            out=root/'runs'/job['id']
            if out.exists():raise RuntimeError('Incomplete block retained; review required: '+str(out))
            while True:
                if (ROOT/'STOP_AFTER_BLOCK').exists():
                    dump(root/'status.json',dict(state='stopped_at_block_boundary',utc=utc(),next_job=job['id']));return
                battery=psutil.sensors_battery()
                ac=battery is None or battery.power_plugged
                if in_window() and seconds_left()>job['scheduling_seconds'] and ac:break
                dump(root/'status.json',dict(state='waiting_for_authorized_window_and_capacity',utc=utc(),next_job=job['id'],
                     required_seconds=job['scheduling_seconds'],window_seconds_left=seconds_left(),ac_power=ac,
                     completed=[j['id'] for j in design['jobs'] if (root/'runs'/j['id']/'verification.json').exists()]))
                time.sleep(30)
            guard(design)
            log=root/(job['id']+'.log');at=time.monotonic()
            with log.open('x',encoding='utf-8') as output:
                child=subprocess.Popen([sys.executable,'-X','utf8','-B',str(ROOT/'campaign.py'),'worker','--run-id',run_id,'--job',job['id']],
                                        cwd=str(ROOT),env={**os.environ,**ENV_THREADS},stdout=output,stderr=subprocess.STDOUT)
                while child.poll() is None:
                    elapsed=time.monotonic()-at
                    dump(root/'status.json',dict(state='running',utc=utc(),job=job,launcher_pid=child.pid,elapsed_seconds=elapsed))
                    if elapsed>7*3600:
                        proc=psutil.Process(child.pid)
                        for descendant in proc.children(recursive=True):descendant.terminate()
                        proc.terminate();raise TimeoutError('Fixed seven-hour job timeout')
                    time.sleep(5)
                if child.returncode!=0:raise RuntimeError(f"Worker failed: {job['id']}; see {log}")
            assert cert.exists() and load(cert)['all_passed']
        dump(root/'status.json',dict(state='complete',utc=utc(),completed=[j['id'] for j in design['jobs']],expected=design['expected']))
        analysis=ROOT/'analysis'/run_id
        assert design['analysis_code']=={name:sha(ROOT/name) for name in design['analysis_code']},'Analysis code changed; measurements remain complete'
        if not (analysis/'independent_verification.json').exists():
            with (root/'analysis.log').open('a',encoding='utf-8') as output:
                subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'analyze_revision.py'),'--campaign',str(root),'--output',str(analysis)],
                               stdout=output,stderr=subprocess.STDOUT,check=True)
                subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'independent_revision.py'),'--campaign',str(root),'--analysis',str(analysis)],
                               stdout=output,stderr=subprocess.STDOUT,check=True)
        assert load(analysis/'independent_verification.json')['all_passed']
        dump(root/'analysis_complete.json',dict(utc=utc(),analysis=str(analysis),independent_verification=entry(analysis/'independent_verification.json')))
    except BaseException:
        destination=root/'failure.json' if root.exists() else ROOT/'campaigns'/('failure_'+run_id+'.json')
        dump(destination,dict(utc=utc(),traceback=traceback.format_exc()));raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        dump(lock,{**identity,'finished_utc':utc()})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','run','worker'])
    parser.add_argument('--run-id',default='revision_v1_20261112');parser.add_argument('--job');parser.add_argument('--wait-validation',action='store_true')
    args=parser.parse_args()
    if args.command=='freeze':freeze(args.run_id)
    elif args.command=='worker':worker(ROOT/'campaigns'/args.run_id,args.job)
    else:run(args.run_id,args.wait_validation)
