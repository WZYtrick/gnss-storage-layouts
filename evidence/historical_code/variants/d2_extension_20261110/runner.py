"""Frozen single-worker experiments with complete-block night-time resume."""
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))
from common import *
import argparse
import gc
import itertools
import platform
import random
import statistics
import subprocess
import time
import traceback
import psutil
import numpy as np
import pyarrow as pa

MECHANISMS = ['C','Cstar','T_time','T','Tperm_time','Tperm_blocksv','C_2048','C_32768']
BOUNDARIES = ['T_time','T','Tperm_time','Tperm_blocksv']
CODE_NAMES = ['common.py','runner.py','reader.py','tasks.py','protocol.md']
DEPENDENCY_PATHS = [Path(s.__file__), OLD/'direct_reader.py']
TZ = dt.timezone(dt.timedelta(hours=8))

AUTHORIZED_RUN_ID = 'd2_extension_20261110'
SOURCE_RUN_ID = 'd2_confirmation_immediate_20261109'
SOURCE_PREFIX_COUNT = 52
BASE_RUN_ID = 'd2_confirmation_20261109'
SESSION_ID = 'authorized_d2_extension_20261110'
TIME_POLICY = 'initial_authorized_window_then_night'
AUTHORIZATION_PATH = ROOT/'frozen/d2_extension_authorization_20261110.json'
WINDOW_START = dt.datetime.fromisoformat('2026-11-10T00:53:00+08:00')
WINDOW_END = dt.datetime.fromisoformat('2026-11-10T08:30:00+08:00')
AFTER_WINDOW_POLICY = 'Asia/Shanghai 23:00-07:00'
_ORIGINAL_ROOT = ROOT

class _RunnerRoot:
    """Only root/runner.py resolves to this isolated operational runner."""
    def __truediv__(self, child):
        return Path(__file__).resolve() if str(child) == 'runner.py' else _ORIGINAL_ROOT/child
    def __fspath__(self): return str(_ORIGINAL_ROOT)
    def __str__(self): return str(_ORIGINAL_ROOT)
    def resolve(self): return _ORIGINAL_ROOT.resolve()

ROOT = _RunnerRoot()

def session_at(now):
    assert now.tzinfo is not None
    now = now.astimezone(TZ)
    if WINDOW_START <= now < WINDOW_END:
        return SESSION_ID, 'authorized_initial_window'
    if now >= WINDOW_END and (now.hour >= 23 or now.hour < 7):
        date = now.date()-dt.timedelta(days=1) if now.hour < 7 else now.date()
        return date.isoformat(), 'night_23_07_after_exception'
    return None, None

def validate_design_scope(design, base):
    assert design['run_id'] == AUTHORIZED_RUN_ID and design['base_run_id'] == BASE_RUN_ID
    assert design['source_run_id'] == SOURCE_RUN_ID and design['source_prefix_count'] == SOURCE_PREFIX_COUNT
    assert design['confirm'] is True and design['time_policy'] == TIME_POLICY
    assert design['authorization_id'] == SESSION_ID
    assert design['authorization_manifest_path'] == str(AUTHORIZATION_PATH)
    assert design['night_hours'] == base['night_hours'] == '23:00-07:00 Asia/Shanghai'
    for key in ['modules','candidates','cells','blocks','repeats','timeout_seconds',
                'launcher_timeout_seconds','between_process_seconds','query_seed','inputs',
                'frozen_inputs','holdout_freeze','worker_estimated_seconds','smoke_id']:
        assert design[key] == base[key], ('Changed scientific matrix or evidence', key)
    assert design['schedule'] == base['schedule'][SOURCE_PREFIX_COUNT:]
    assert design['processes'] == 56
    assert design['expected_calls'] == dict(timed=72576,warmup=24192,verified=96768)
    assert base['schedule'][SOURCE_PREFIX_COUNT-1]['module'] == 'E2'
    assert base['schedule'][SOURCE_PREFIX_COUNT-1]['block'] == 3
    assert design['schedule'][0]['module'] == 'E2' and design['schedule'][0]['block'] == 4
    policy = design['measurement_policy']
    assert policy['kind'] == TIME_POLICY and policy['authorization_id'] == SESSION_ID
    assert dt.datetime.fromisoformat(policy['allowed_start_utc']) == WINDOW_START
    assert dt.datetime.fromisoformat(policy['allowed_end_utc']) == WINDOW_END
    assert policy['after_window_policy'] == AFTER_WINDOW_POLICY
    assert Path(policy['authorization']['path']) == AUTHORIZATION_PATH
    expected_code = [entry(Path(__file__)) if Path(item['path']) == _ORIGINAL_ROOT/'runner.py' else item for item in base['code']]
    assert design['code'] == expected_code, 'Only the isolated runner may replace a frozen dependency'
    assert design['validation'][:len(base['validation'])] == base['validation']
    return True


def validate_source_prefix(binding, base):
    verify_entries([binding])
    prefix = load(binding['path'])
    assert prefix['all_passed'] is True and prefix['source_run_id'] == SOURCE_RUN_ID
    assert prefix['prefix_configurations'] == SOURCE_PREFIX_COUNT
    expected_keys = [dict(cell=r['cell'],block=r['block']) for r in base['schedule'][:SOURCE_PREFIX_COUNT]]
    assert prefix['completed_keys'] == expected_keys
    assert prefix['source_expected_calls'] == dict(timed=147744,warmup=49248,verified=196992)
    expected_paths = {'original_schedule':_ORIGINAL_ROOT/'frozen'/f'{BASE_RUN_ID}.json',
                      'source_design':_ORIGINAL_ROOT/'frozen'/f'{SOURCE_RUN_ID}.json',
                      'source_status':_ORIGINAL_ROOT/'campaigns'/SOURCE_RUN_ID/'status.json'}
    for key, expected in expected_paths.items():
        assert Path(prefix[key]['path']).resolve() == expected.resolve()
    verify_entries([prefix[k] for k in ['original_schedule','source_design','source_status','checkpoint_evidence']])
    old = load(prefix['source_status']['path'])
    assert old['run_id'] == SOURCE_RUN_ID and old['state'] in ('paused_at_block_boundary','block_complete')
    assert [dict(cell=r['cell'],block=r['block']) for r in old['completed']] == expected_keys
    assert all(r['exit_code'] == 0 and r['monitor_complete'] and not r['errors'] for r in old['completed'])
    audit = load(prefix['checkpoint_evidence']['path'])
    assert audit['all_passed'] is True and audit['configurations'] == SOURCE_PREFIX_COUNT
    assert audit['verified_calls'] == 196992
    return prefix


def validate_authorization(design):
    base_path = _ORIGINAL_ROOT/'frozen'/f'{BASE_RUN_ID}.json'
    assert design['base_frozen']['path'] == str(base_path.resolve())
    verify_entries([design['base_frozen'], design['measurement_policy']['authorization']])
    base = load(base_path)
    validate_design_scope(design, base)
    verify_entries(base['code'])
    auth = load(AUTHORIZATION_PATH)
    assert auth['run_id'] == AUTHORIZED_RUN_ID and auth['authorization_id'] == auth['session_id'] == SESSION_ID
    assert auth['time_policy'] == TIME_POLICY and auth['after_window_policy'] == AFTER_WINDOW_POLICY
    assert auth['base_frozen'] == design['base_frozen'] and auth['holdout_freeze'] == design['holdout_freeze']
    assert auth['source_prefix'] == design['source_prefix']
    assert auth['source_run_id'] == SOURCE_RUN_ID and auth['source_prefix_count'] == SOURCE_PREFIX_COUNT
    validate_source_prefix(auth['source_prefix'],base)
    assert dt.datetime.fromisoformat(auth['allowed_start_utc']) == WINDOW_START
    assert dt.datetime.fromisoformat(auth['allowed_end_utc']) == WINDOW_END
    assert auth['expected_configurations'] == 56 and auth['expected_calls'] == design['expected_calls']
    assert auth['target_groups'] == [dict(module=m,block=b) for m,b in dict.fromkeys((r['module'],r['block']) for r in design['schedule'])]
    assert auth['runner_argv'] == [str(PYTHON),'-B','-X','utf8',str(Path(__file__).resolve()),'benchmark','--run-id',AUTHORIZED_RUN_ID,'--max-blocks','1']
    assert load(_ORIGINAL_ROOT/'frozen'/f'{AUTHORIZED_RUN_ID}.json') == design
    verify_entries(auth['code'] + [auth['holdout_freeze'],auth['equivalence_check'],auth['amendment']])
    assert auth['equivalence_check'] in design['validation']
    assert design['measurement_policy']['authorization'] in design['validation']
    assert auth['source_prefix'] in design['validation']
    return auth


def local_now(): return dt.datetime.now(TZ)
def night_label():
    label, _ = session_at(local_now())
    return label if label is not None else 'outside_authorized_measurement_window'
def in_night(): return session_at(local_now())[0] is not None
def night_remaining():
    now=local_now()
    label, policy=session_at(now)
    if label is None: return 0
    if policy=='authorized_initial_window': end=WINDOW_END
    else:
        end=now.replace(hour=7,minute=0,second=0,microsecond=0)
        if now.hour>=23: end+=dt.timedelta(days=1)
    return max(0,(end-now).total_seconds())
def union(*groups): return list(dict.fromkeys(itertools.chain.from_iterable(groups)))

def define_cells(modules, extras=(), confirm=False):
    cells={}
    core=union(CORE,extras)
    main='D2' if confirm else 'D1'
    mech='D2m' if confirm else 'D0m'
    def add(mod,dataset,cpus,contracts,methods,kind='query',dates=None):
        m,qs,truth,layouts=inputs(dataset)
        if kind=='task':qs=load(ROOT/'inputs'/dataset/'tasks.json')
        if dates:qs=[q for q in qs if q['date'] in dates]
        missing=set(methods)-set(layouts)
        assert not missing,(mod,dataset,'missing layouts',missing)
        for cpu,contract in itertools.product(cpus,contracts):
            name=f'{mod}_{dataset}_cpu{cpu:02d}_{contract}'
            cells[name]=dict(module=mod,dataset=dataset,cpu=cpu,contract=contract,methods=methods,
                kind=kind,query_ids=[q['id'] for q in qs],repeats=3,warmup_passes=2 if kind=='calibration' else 1)
    for mod in modules:
        if mod=='E1':add(mod,main,[1,8] if confirm else [1,4,8,16],CONTRACTS,core)
        elif mod=='E2':
            add(mod,mech,[1,8],['unordered_arrow'],union(CORE,MECHANISMS))
            add(mod,mech,[8],CONTRACTS[:2],union(CORE,BOUNDARIES))
        elif mod=='E3':add(mod,mech,[1,8],['unordered_arrow'],union(CORE,['C_full','W_packed']))
        elif mod=='E4':
            assert not confirm
            add(mod,'D1',[1,8],CONTRACTS[:2],['W','Cstar','F'],'calibration',['2024-10-01','2024-10-04'])
        elif mod=='E5':add(mod,main,[1,8],CONTRACTS,core,'task')
        else:raise ValueError(mod)
    return cells

def make_design(modules,extras=(),confirm=False):
    cells=define_cells(modules,extras,confirm);schedule=[]
    for mod in modules:
        names=[k for k,v in cells.items() if v['module']==mod]
        rng=random.Random(627000+int(mod[1:])*100);rng.shuffle(names)
        for block in range(6):
            off=(block//2)%len(names);order=names[off:]+names[:off]
            if block%2:order.reverse()
            for name in order:
                c=cells[name]
                schedule.append(dict(cell=name,block=block,module=mod,dataset=c['dataset'],cpu=c['cpu'],
                    contract=c['contract'],run_directory=f'b{block:02d}_{name}'))
    timed=warmup=0
    for spec in schedule:
        c=cells[spec['cell']];n=len(c['query_ids'])*len(c['methods'])
        timed+=n*(4 if c['kind']=='calibration' else 3)
        warmup+=n*c['warmup_passes']
    return dict(created=utc(),modules=modules,confirm=confirm,candidates=union(CORE,extras),cells=cells,
        schedule=schedule,blocks=6,repeats=3,expected_calls=dict(timed=timed,warmup=warmup,verified=timed+warmup),
        processes=len(schedule),night_hours='23:00-07:00 Asia/Shanghai',timeout_seconds=3600,
        launcher_timeout_seconds=3650,between_process_seconds=30,query_seed=627100,
        code=[entry(ROOT/n) for n in CODE_NAMES]+[entry(p) for p in DEPENDENCY_PATHS])

def code_matches(design):
    validate_authorization(design)
    verify_entries(design['code'])

def input_path(design,dataset,name):
    if 'frozen_inputs' in design:return Path(design['frozen_inputs'][dataset][name])
    return ROOT/'inputs'/dataset/(name+'.json')

def campaign_inputs(design,dataset):
    return tuple(load(input_path(design,dataset,n)) for n in ['sources','queries','truth','layouts'])

def collect_inputs(cells):
    paths=set()
    for dataset in {c['dataset'] for c in cells.values()}:
        directory=ROOT/'inputs'/dataset
        # Mutable input indices are snapshotted separately before a run; artifacts remain immutable.
        m,qs,truth,layouts=inputs(dataset)
        used=union(*(c['methods'] for c in cells.values() if c['dataset']==dataset))
        for method in used:
            layout=Path(layouts[method])
            for p in layout.iterdir():
                if p.is_file():paths.add(p.resolve())
        for src in m['sources']:
            for key in ['source_file','compressed_file']:
                if key in src:paths.add(Path(src[key]).resolve())
            for name in ['source.json','v.npy','l.npy','s.npy','t.npy','sv.npy','epoch_flag.npy']:
                p=Path(src['prepared_dir'])/name
                if p.exists():paths.add(p.resolve())
    return [entry(p) for p in sorted(paths)]

def environment(spec):
    proc=psutil.Process();battery=psutil.sensors_battery()
    power=subprocess.run(['powercfg','/getactivescheme'],capture_output=True,text=True,errors='replace').stdout.strip()
    return dict(utc=utc(),pid=os.getpid(),process_create_time=proc.create_time(),dataset=spec['dataset'],
        module=spec['module'],contract=spec['contract'],python=sys.version,numpy=np.__version__,pyarrow=pa.__version__,
        actual_cpu_pool=pa.cpu_count(),actual_io_pool=pa.io_thread_count(),platform=platform.platform(),
        affinity=proc.cpu_affinity(),power_scheme=power,power_plugged=None if battery is None else battery.power_plugged,
        thread_environment={k:os.environ.get(k) for k in ENV_THREADS},runner_sha256=sha(Path(__file__)),
        code={n:sha(ROOT/n) for n in CODE_NAMES},night=night_label(),logical_cpus=psutil.cpu_count(),
        total_memory_bytes=psutil.virtual_memory().total,temperature_available=False,dynamic_frequency_available=False, measurement_session=night_label(), time_policy=session_at(local_now())[1],
        campaign_time_policy=TIME_POLICY, authorization_manifest=entry(AUTHORIZATION_PATH),
        original_runner=entry(_ORIGINAL_ROOT/'runner.py'), operational_runner=entry(Path(__file__)))

def pick_smoke(qs,truth,kind):
    if kind=='task':
        picked=[]
        for task in sorted({q['task'] for q in qs}):
            for window in sorted({q['end']-q['start'] for q in qs if q['task']==task}):
                picked.append(next(q for q in qs if q['task']==task and q['end']-q['start']==window))
        return picked
    picked=[]
    for family in sorted({q['family'] for q in qs}):
        candidates=[q for q in qs if q['family']==family]
        picked.append(next((q for q in candidates if truth[q['id']]['rows']),candidates[0]))
        empty=next((q for q in candidates if not truth[q['id']]['rows']),None)
        if empty is not None and empty not in picked:picked.append(empty)
    return picked

def measure(run_id,cell,block,smoke=False):
    from reader import Reader, fingerprint_result
    from tasks import run_task, task_fingerprint
    campaign=ROOT/('smoke' if smoke else 'campaigns')/run_id
    design=load(campaign/'design.json');code_matches(design);spec=design['cells'][cell]
    if design['confirm']:
        assert 'holdout_freeze' in design
        verify_entries([design['holdout_freeze']])
    directory=campaign/'runs'/f'b{block:02d}_{cell}';directory.mkdir(parents=True,exist_ok=False)
    m,qs,truth,layouts=campaign_inputs(design,spec['dataset'])
    if spec['kind']=='task':
        qs=load(input_path(design,spec['dataset'],'tasks'))
        task_truth=load(input_path(design,spec['dataset'],'task_truth'))
    qs=[q for q in qs if q['id'] in set(spec['query_ids'])]
    if smoke:qs=pick_smoke(qs,truth,spec['kind'])
    pa.set_cpu_count(spec['cpu']);pa.set_io_thread_count(4)
    assert pa.cpu_count()==spec['cpu'] and pa.io_thread_count()==4
    assert sys.version.startswith('3.12.14') and np.__version__=='2.3.5' and pa.__version__=='25.0.1'
    dump(directory/'environment.json',environment(spec))
    readers={method:Reader(Path(layouts[method]),m,cpu=spec['cpu']) for method in spec['methods']}
    rng=random.Random(627100+block)
    verified=timed=warmup=0;start=time.monotonic()
    repeats=[0] if smoke else ([-1,0] if spec['kind']=='calibration' else [-1,0,1,2])
    dump(directory/'design.json',dict(cell=cell,block=block,spec=spec,queries=[q['id'] for q in qs],purpose='smoke' if smoke else 'formal'))
    def call(q,method,repeat,profile,position,quad_id):
        nonlocal verified,timed,warmup
        if time.monotonic()-start>3600:raise TimeoutError('Fixed worker 3600 second timeout')
        if not smoke and not in_night():raise RuntimeError('Night window ended; this incomplete block is invalid')
        started_utc=utc()
        before_cpu=time.process_time();task_begin=time.perf_counter()
        actual,stats=readers[method].query(q,spec['contract'],profile=profile)
        if spec['kind']=='task':
            task_begin2=time.perf_counter();result=run_task(actual,q,m,spec['contract']);finished=time.perf_counter()
            stats['task_seconds']=finished-task_begin2
            stats['end_to_end_seconds']=finished-task_begin
            ended_utc=utc()
            digest=task_fingerprint(result);n=result.num_rows
            expected=task_truth[q['id']]
            assert digest==expected['sha256'] and n==expected['rows'],('task result mismatch',cell,method,q['id'])
            del result
        else:
            ended_utc=utc()
            digest=fingerprint_result(actual,q,spec['contract'])
            n=actual.num_rows if spec['contract']=='unordered_arrow' else len(actual['rid'])
            expected=truth[q['id']]
            assert digest==expected['sha256'] and n==expected['rows'],('result mismatch',cell,method,q['id'])
        cpu_time=time.process_time()-before_cpu
        del actual
        wall_seconds=time.perf_counter()-task_begin
        if not smoke and not in_night():raise RuntimeError('Night window ended during call; this incomplete block is invalid')
        is_warm=repeat<0;verified+=1;warmup+=int(is_warm);timed+=int(not is_warm)
        return dict(module=spec['module'],dataset=spec['dataset'],cell=cell,block=block,night=night_label(),
            cpu=spec['cpu'],contract=spec['contract'],method=method,query=q['id'],family=q.get('family',q.get('task')),
            task_kind=q.get('task'),round=repeat,repeat=repeat,warmup=is_warm,profile=profile,
            started_utc=started_utc,ended_utc=ended_utc,call_with_validation_seconds=wall_seconds,
            call_order=verified-1,sequence_position=position,quad_id=quad_id,rows=n,sha256=digest,cpu_with_validation_seconds=cpu_time,
            month=q.get('month'),station_cluster=q.get('station_cluster'),**stats)
    try:
        with (directory/'timings.jsonl').open('x',encoding='utf-8') as log:
            for repeat in repeats:
                gc.collect();jobs=list(qs);rng.shuffle(jobs)
                for qi,q in enumerate(jobs):
                    # Hash ordering keeps common-layout relative order identical when a cell adds controls.
                    methods=sorted(spec['methods'],key=lambda x:hashlib.sha256(f'627100:{block}:{repeat}:{q["id"]}:{x}'.encode()).digest())
                    for method in methods:
                        if spec['kind']=='calibration':
                            parity=int(hashlib.sha256(f'{q["id"]}:{method}'.encode()).hexdigest()[:8],16)%2
                            modes=[False,True] if repeat<0 else ([False,True,True,False] if (parity+block)%2==0 else [True,False,False,True])
                            quad=f'{cell}:b{block}:{q["id"]}:{method}'
                            records=[call(q,method,-1 if repeat<0 else pos,on,pos,quad) for pos,on in enumerate(modes)]
                        else:records=[call(q,method,repeat,False,0,None)]
                        for row in records:log.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
                    if (qi+1)%12==0 or qi+1==len(jobs):
                        log.flush();dump(directory/'progress.json',dict(utc=utc(),pid=os.getpid(),query=qi+1,
                            total=len(jobs),round=repeat,verified_calls=verified,timed_calls=timed,elapsed_seconds=time.monotonic()-start))
                print(cell,'block',block,'round',repeat,'verified',verified,'seconds',round(time.monotonic()-start,1),flush=True)
        code_matches(design)
        dump(directory/'verification.json',dict(complete=True,all_results_match=True,verified_calls=verified,
            timed_calls=timed,warmup_calls=warmup,elapsed_seconds=time.monotonic()-start,ended=utc()))
    except BaseException:
        dump(directory/'failure.json',dict(utc=utc(),traceback=traceback.format_exc(),verified_calls=verified,timed_calls=timed));raise

def monitor(child,run_dir,path,design,start):
    launcher=psutil.Process(child.pid);launched=launcher.create_time();worker=None;errors=[];samples=0;timeout=False
    psutil.cpu_percent(percpu=True)
    def alive(proc):
        try:return proc is not None and proc.is_running() and proc.status()!=psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:return False
    def stop_owned_tree():
        owned=[]
        try:
            if alive(launcher):owned=launcher.children(recursive=True)
        except psutil.NoSuchProcess:pass
        if worker is not None and all(x.pid!=worker.pid for x in owned):owned.append(worker)
        if alive(launcher):owned.insert(0,launcher)
        for proc in reversed(owned):
            try:
                if alive(proc):proc.kill()
            except psutil.NoSuchProcess:pass
        _,surviving=psutil.wait_procs(owned,timeout=10)
        if surviving:errors.append('Owned process survivors: '+str([x.pid for x in surviving]))
    with path.open('x',encoding='utf-8') as out:
        while child.poll() is None or alive(worker):
            if time.monotonic()-start>3650:
                timeout=True
                print('HARD TIMEOUT: stopping verified experiment process tree',flush=True)
                stop_owned_tree()
                break
            env_file=run_dir/'environment.json'
            if worker is None and env_file.exists():
                try:
                    env=load(env_file);candidate=psutil.Process(env['pid'])
                    assert candidate.pid==child.pid or child.pid in [x.pid for x in candidate.parents()]
                    assert abs(candidate.create_time()-env['process_create_time'])<.001
                    assert candidate.create_time()>=launched-1
                    assert env['runner_sha256']==next(x['sha256'] for x in design['code'] if Path(x['path']).name=='runner.py')
                    assert str(Path(__file__)).lower() in [x.lower() for x in candidate.cmdline()]
                    worker=candidate;worker.cpu_percent()
                    dump(run_dir/'worker_identity.json',dict(launcher_pid=child.pid,worker_pid=worker.pid,create_time=worker.create_time(),verified=True))
                except BaseException as exc:
                    errors.append(repr(exc));break
            rec=dict(utc=utc(),elapsed=time.monotonic()-start,cpu_per_logical=psutil.cpu_percent(percpu=True),available_bytes=psutil.virtual_memory().available)
            if worker:
                try:
                    rec.update(pid=worker.pid,rss_bytes=worker.memory_info().rss,cpu_percent=worker.cpu_percent(),
                        threads=worker.num_threads(),cpu_times=worker.cpu_times()._asdict(),io=worker.io_counters()._asdict());samples+=1
                except psutil.NoSuchProcess:break
            out.write(json.dumps(rec)+'\n');out.flush();time.sleep(1)
    try:code=child.wait(timeout=max(1,3650-(time.monotonic()-start)) if errors else 15)
    except subprocess.TimeoutExpired:
        timeout=True;stop_owned_tree();code=child.wait(timeout=15)
    worker_exit=None
    if worker is not None:
        try:worker_exit=worker.wait(timeout=10)
        except psutil.TimeoutExpired:
            errors.append('Worker survived launcher exit');stop_owned_tree()
        except psutil.NoSuchProcess:pass
    exited=worker is not None and not alive(worker)
    result=dict(launcher_exit_code=code,worker_exit_code=worker_exit,worker_exited=exited,
        worker_samples=samples,errors=errors,hard_timeout=timeout,
        monitor_complete=code==0 and worker_exit in [0,None] and exited and samples>0 and not errors and not timeout)
    dump(path.with_suffix('.verification.json'),result)
    return result

def preflight(directory):
    psutil.cpu_percent();samples=[]
    for _ in range(30):
        time.sleep(1);battery=psutil.sensors_battery()
        samples.append(dict(cpu=psutil.cpu_percent(),available=psutil.virtual_memory().available,
            plugged=None if battery is None else battery.power_plugged))
    result=dict(created=utc(),samples=samples,mean_cpu=statistics.fmean(x['cpu'] for x in samples),
        min_available=min(x['available'] for x in samples),plugged=all(x['plugged'] is not False for x in samples))
    result['passed']=result['mean_cpu']<10 and result['min_available']>=8*1024**3 and result['plugged']
    dump(directory/f'preflight_{local_now():%Y%m%d_%H%M%S}.json',result)
    return result['passed']

def run_worker(root,design,spec,run_id,smoke):
    run_dir=root/'runs'/spec['run_directory'];log_path=root/'logs'/f'{spec["run_directory"]}.log'
    start=time.monotonic()
    cmd=[str(PYTHON),'-B','-X','utf8',str(Path(__file__)),'measure','--run-id',run_id,'--cell',spec['cell'],'--block',str(spec['block'])]
    if smoke:cmd.append('--smoke-worker')
    with log_path.open('x',encoding='utf-8') as log:
        child=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,**ENV_THREADS},creationflags=subprocess.CREATE_NO_WINDOW)
        result=monitor(child,run_dir,root/'logs'/f'{spec["run_directory"]}_resources.jsonl',design,start)
    verification=load(run_dir/'verification.json') if (run_dir/'verification.json').exists() else {}
    result.update(**spec,exit_code=result['launcher_exit_code'] if result['launcher_exit_code'] else (0 if result['monitor_complete'] and verification.get('complete') else 97),
        elapsed_seconds=time.monotonic()-start,night=night_label(),log=str(log_path))
    return result

def smoke(run_id,modules,extras,confirm):
    design=make_design(modules,extras,confirm);root=ROOT/'smoke'/run_id
    if confirm:
        p=ROOT/'frozen/D2_predictions.json'
        assert p.exists(),'D2 predictions must be frozen before any D2 performance smoke'
        assert set(load(p)['candidates'])==set(design['candidates'])
        design['holdout_freeze']=entry(p)
    root.mkdir(parents=True,exist_ok=False);(root/'runs').mkdir();(root/'logs').mkdir();dump(root/'design.json',design)
    done=[]
    for cell,spec in design['cells'].items():
        stage=dict(cell=cell,block=0,run_directory=f'b00_{cell}',**{k:spec[k] for k in ['module','dataset','cpu','contract']})
        print('SMOKE START',cell,flush=True);result=run_worker(root,design,stage,run_id,True);done.append(result)
        dump(root/'status.json',dict(state='failed' if result['exit_code'] else 'running',completed=done))
        if result['exit_code']:raise SystemExit(result['exit_code'])
    dump(root/'verification.json',dict(all_passed=True,code=design['code'],cells=list(design['cells']),completed=done))
    dump(root/'status.json',dict(state='complete',completed=done))

def freeze(run_id,smoke_id,modules,extras,confirm):
    target=ROOT/'frozen'/f'{run_id}.json';assert not target.exists()
    design=make_design(modules,extras,confirm)
    checked=load(ROOT/'smoke'/smoke_id/'verification.json');assert checked['all_passed']
    verify_entries(checked['code']);assert set(checked['cells'])==set(design['cells'])
    validation=[]
    for name in ['reader_validation.json','task_validation.json','design_checks.json']:
        p=ROOT/'validation'/name;assert p.exists(),name
        report=load(p);assert report.get('all_passed') is True or report.get('passed') is True,name
        if 'code' in report:verify_entries(report['code'])
        validation.append(entry(p))
    for dataset in {c['dataset'] for c in design['cells'].values()}:
        p=ROOT/'validation'/f'{dataset}_layout_invariants.json';assert p.exists(),str(p)
        report=load(p);assert report.get('all_passed') is True,str(p)
        assert report['dataset']==dataset
        m,qs,truth,layouts=inputs(dataset)
        required_methods=set(union(*(c['methods'] for c in design['cells'].values() if c['dataset']==dataset)))
        assert required_methods<=set(report['methods'])
        assert set(report['source_ids'])=={src['id'] for src in m['sources']}
        assert set(report['query_ids'])=={q['id'] for q in qs}
        checked_full={(r['method'],r['source_id']) for r in report['content'] if r['scope']=='full_source' and r['passed']}
        checked_queries={(r['method'],r['query']) for r in report['content'] if r['scope']=='frozen_query' and r['passed']}
        assert set(itertools.product(required_methods,report['source_ids']))<=checked_full
        assert set(itertools.product(required_methods,report['query_ids']))<=checked_queries
        verify_entries(report['inputs']);verify_entries(report['code']);validation.append(entry(p))
    design.update(run_id=run_id,smoke_id=smoke_id,validation=validation,inputs=collect_inputs(design['cells']))
    if confirm:
        p=ROOT/'frozen/D2_predictions.json';assert p.exists(),'D2 predictions must precede D2 formal timing'
        holdout=load(p);assert set(holdout['candidates'])==set(design['candidates'])
        design['holdout_freeze']=entry(p)
    archive=ROOT/'frozen'/f'{run_id}_code';archive.mkdir(parents=True,exist_ok=False)
    import shutil
    for item in design['code']:
        p=Path(item['path']);name=p.name if p.parent==ROOT else p.parent.name+'__'+p.name
        shutil.copy2(p,archive/name)
    # Preserve the exact indices and validation reports when later candidate completion appends layouts.
    design['frozen_inputs']={}
    for dataset in {c['dataset'] for c in design['cells'].values()}:
        target_dir=archive/'inputs'/dataset;target_dir.mkdir(parents=True)
        design['frozen_inputs'][dataset]={}
        for source in (ROOT/'inputs'/dataset).glob('*.json'):
            target_file=target_dir/source.name;shutil.copy2(source,target_file)
            design['frozen_inputs'][dataset][source.stem]=str(target_file.resolve())
            design['inputs'].append(entry(target_file))
    originals=design['validation'];frozen_validation=[]
    (archive/'validation').mkdir()
    for original in originals:
        target_file=archive/'validation'/Path(original['path']).name
        shutil.copy2(original['path'],target_file);frozen_validation.append(entry(target_file))
    design['validation_originals']=originals
    design['validation']=frozen_validation
    # Extrapolate smoke call+validation wall time by class/window, not a fixed first-block guess.
    smoke_root=ROOT/'smoke'/smoke_id
    estimates={}
    for cell,c in design['cells'].items():
        run=smoke_root/'runs'/f'b00_{cell}'
        rows=[json.loads(x) for x in (run/'timings.jsonl').read_text(encoding='utf-8').splitlines()]
        assert [row['call_order'] for row in rows]==list(range(len(rows))),('smoke call order',cell)
        manifest,queries,truth,layouts=inputs(c['dataset'])
        if c['kind']=='task':queries=load(ROOT/'inputs'/c['dataset']/'tasks.json')
        qmap={q['id']:q for q in queries}
        def group(q):
            return (q['task'],q['end']-q['start']) if c['kind']=='task' else (q['family'],)
        samples={}
        for r in rows:samples.setdefault((r['method'],group(qmap[r['query']])),[]).append(r['call_with_validation_seconds'])
        passes=6 if c['kind']=='calibration' else 4
        total=0
        for qid in c['query_ids']:
            for method in c['methods']:
                v=samples[method,group(qmap[qid])]
                total+=max(v)*passes
        observed=load(run/'verification.json')['elapsed_seconds']
        init_overhead=max(0,observed-sum(r['call_with_validation_seconds'] for r in rows))
        estimates[cell]=max(30,(total+init_overhead)*1.5+10)
    design['worker_estimated_seconds']=estimates
    dump(target,design);print('FROZEN',run_id,design['expected_calls'],flush=True)

def benchmark(run_id,max_blocks=None):
    design=load(ROOT/'frozen'/f'{run_id}.json');code_matches(design)
    verify_entries(design['inputs']);verify_entries(design['validation'])
    root=ROOT/'campaigns'/run_id
    if not root.exists():
        root.mkdir(parents=True);(root/'runs').mkdir();(root/'logs').mkdir();dump(root/'design.json',design)
        dump(root/'status.json',dict(state='ready',run_id=run_id,completed=[]))
    assert load(root/'design.json')==design
    state=load(root/'status.json');assert state['state'] not in ['failed','blocked'],'Inspect failure and create an explicit fresh run; no silent retry'
    done=state['completed'];completed={(x['cell'],x['block']) for x in done if x['exit_code']==0}
    if len(completed)==len(design['schedule']):print('Already complete');return
    def save(status,**extra):
        new=dict(state=status,run_id=run_id,completed=done,updated=utc(),**extra)
        new.update(time_policy=TIME_POLICY, authorization_id=SESSION_ID, authorization_manifest=entry(AUTHORIZATION_PATH))
        dump(root/'status.json',new)
    if not in_night():save('waiting_for_night');print('WAITING FOR NIGHT 23:00-07:00 Asia/Shanghai');return
    if not preflight(root):save('waiting_for_quiet');print('WAITING FOR QUIET');return
    groups=[]
    for key,items in itertools.groupby(design['schedule'],key=lambda x:(x['module'],x['block'])):
        group=list(items)
        if any((x['cell'],x['block']) not in completed for x in group):groups.append((key,group))
    finished_blocks=0
    for (mod,block),group in groups:
        assert all((x['cell'],x['block']) not in completed for x in group),'Partial block requires explicit failed-run recovery; never silently splice'
        previous=[x for x in done if x['module']==mod]
        smoke_estimate=sum(design['worker_estimated_seconds'][x['cell']] for x in group)
        observed=sum(x['elapsed_seconds'] for x in previous)/max(1,len({x['block'] for x in previous}))
        estimate=max(600,smoke_estimate,observed*1.3)+30*len(group)
        if not in_night() or night_remaining()<estimate:
            save('waiting_for_night',next_module=mod,next_block=block,estimated_block_seconds=estimate);return
        if max_blocks is not None and finished_blocks>=max_blocks:save('paused_at_block_boundary');return
        for spec in group:
            if done:time.sleep(30)
            if not in_night():
                save('failed',reason='Night boundary reached inside block; partial block is invalid',invalid_block=dict(module=mod,block=block));return
            code_matches(design)
            save('running',stage=spec,master_pid=os.getpid())
            print('START',spec['run_directory'],len(done)+1,'/',len(design['schedule']),flush=True)
            result=run_worker(root,design,spec,run_id,False);done.append(result)
            if result['exit_code']:
                save('failed',failed_stage=spec);raise SystemExit(result['exit_code'])
            completed.add((spec['cell'],spec['block']));save('running')
            print('COMPLETE',spec['run_directory'],round(result['elapsed_seconds'],1),'s',flush=True)
        finished_blocks+=1;save('block_complete',last_module=mod,last_block=block)
    verify_entries(design['inputs']);save('complete',ended=utc());print('CAMPAIGN COMPLETE',run_id,flush=True)

def main():
    p=argparse.ArgumentParser(description='Separately authorized D2 window, then normal nights; no measurement-code changes')
    p.add_argument('command',choices=['measure','benchmark'])
    p.add_argument('--run-id',required=True,choices=[AUTHORIZED_RUN_ID])
    p.add_argument('--cell');p.add_argument('--block',type=int)
    p.add_argument('--max-blocks',type=int)
    a=p.parse_args()
    design=load(ROOT/'frozen'/f'{a.run_id}.json');code_matches(design)
    if a.command=='measure':
        assert (a.cell,a.block) in {(r['cell'],r['block']) for r in design['schedule']}
        assert a.max_blocks is None
        measure(a.run_id,a.cell,a.block,False)
    else:
        assert a.cell is None and a.block is None
        assert a.max_blocks is None or a.max_blocks>0
        benchmark(a.run_id,a.max_blocks)

if __name__=='__main__':main()
