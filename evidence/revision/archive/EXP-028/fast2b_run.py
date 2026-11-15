"""Offline runner with group checkpoints and user-controlled pause/resume."""
from fast2b_support import *
import argparse
import ctypes
import gc
import platform
import subprocess
import traceback

SOFT_PAUSE = 75


def freeze():
    import fast2_support as prior
    assert not (RUN/'design.json').exists()
    previous=prior.verify_frozen();assert not alive(load(PREVIOUS/'controller.json'))
    old=load(OLD/'design.json')
    tests=load(ROOT/'validation/fast2b_tests.json');assert tests['all_passed']
    assert tests['fast_code']=={name:sha(ROOT/name) for name in FAST_FILES},'Re-run tests after code changes'
    smoke=load(ROOT/'validation/fast2b_smoke.json');assert smoke['all_passed']
    assert smoke['fast_code']==tests['fast_code'],'Re-run the real-data smoke check after code changes'
    manifest=load(PREVIOUS/'source_manifest.json');manifest['profile']=PROFILE
    for item in manifest['baselines']:
        if item['origin']!='legacy':item.update(origin='fast2b',path=str(RUN/'runs'/item['id']))
    save(RUN/'source_manifest.json',manifest)
    jobs=previous['jobs'];plans={};inherited={};evidence=[]
    old_status=load(PREVIOUS/'status.json');old_finished=load(PREVIOUS/'controller_finished.json')
    assert old_status['state']=='interrupted' and old_finished['child_exited']
    evidence.extend(entry(PREVIOUS/name) for name in ['design.json','source_manifest.json','budget.json','status.json','controller_finished.json','controller.json'])
    for job in jobs:
        groups=load(PREVIOUS/'schedules'/(job['id']+'.json'))
        assert groups==schedule(inputs(job['dataset'])[1],job['block'])
        target=RUN/'schedules'/(job['id']+'.json');save(target,groups);plans[job['id']]=entry(target)
        oldout=PREVIOUS/'runs'/job['id']
        if not oldout.exists():continue
        cp=validate_checkpoint(oldout,groups);newout=RUN/'runs'/job['id'];newout.mkdir(parents=True,exist_ok=True)
        assert cp['next_group']==469 and job['id']=='baseline_D2_b00'
        (newout/'checkpoint.json').write_bytes((oldout/'checkpoint.json').read_bytes())
        evidence.append(entry(oldout/'checkpoint.json'));inherited[job['id']]={}
        for seg in sorted((oldout/'segments').glob('seg_*')):
            env=load(seg/'environment.json');assert not alive(env['process'])
            item=dict(path=str(seg),original_design=entry(PREVIOUS/'design.json'))
            evidence.extend(entry(p) for p in seg.rglob('*') if p.is_file())
            if not (seg/'finished.json').exists():
                progress=load(oldout/'progress.json');assert progress['segment_id']==seg.name and progress['phase']=='recovery'
                assert old_status['job']==job['id'] and 'Frozen phase timeout' in old_status['traceback']
                audit=dict(kind='controller_observed_termination',retrospective=True,recorded_utc=utc(),
                    started_utc=load(seg/'started.json')['started_utc'],ended_utc=old_finished['utc'],process=env['process'],
                    worker_exited=True,returncode=None,reason='Previous supervisor enforced the 7200-second recovery limit',
                    observer=old_finished['process'],evidence=[entry(PREVIOUS/'status.json'),entry(PREVIOUS/'controller_finished.json'),
                      entry(oldout/'progress.json'),entry(seg/'started.json'),entry(seg/'environment.json')])
                target=RUN/'inherited_termination_audits'/(job['id']+'_'+seg.name+'.json');save(target,audit)
                item['termination_audit']=entry(target);evidence.append(entry(target))
            inherited[job['id']][seg.name]=item
    prep=load(RUN/'preparation.json');ledger=load(PREVIOUS/'budget.json')
    assert all(p.get('resumed_utc') for p in ledger['pauses'])
    save(RUN/'budget.json',ledger)
    elapsed_at_extension=elapsed_active(ledger,dt.datetime.fromisoformat(prep['implementation_started_utc']))
    initial=dict(desired='running',revision=0,received_utc=utc(),user_instruction=prep['user_instruction'])
    save(RUN/'commands/000000.json',initial);save(RUN/'control.json',initial)
    design=dict(profile=PROFILE,created_utc=utc(),started_utc=ledger['started_utc'],old_design=entry(OLD/'design.json'),
                previous_design=entry(PREVIOUS/'design.json'),inherited_segments=inherited,inherited_evidence=evidence,
                tests=entry(ROOT/'validation/fast2b_tests.json'),smoke=entry(ROOT/'validation/fast2b_smoke.json'),
                source_manifest=entry(RUN/'source_manifest.json'),schedules=plans,jobs=jobs,
                fast_code={name:sha(ROOT/name) for name in FAST_FILES},time_policy=POLICY,
                active_budget_seconds=elapsed_at_extension+35*3600,measurement_budget_seconds=elapsed_at_extension+32*3600,
                scheduled_block_limit=None,recovery_round_limit=None,original_runtime_code=old['runtime_code'],
                budget_extension=dict(implementation_started_utc=prep['implementation_started_utc'],prior_effective_seconds=elapsed_at_extension,
                     remaining_measurement_seconds_at_implementation=32*3600,analysis_reserve_seconds=3*3600,
                     policy='Only the overall effective-time limit can interrupt a slow group; phase caps are removed.'),
                instruction=prep['user_instruction'],changed_scope='Timing authorization and termination provenance only; requests, readers, ordering, repeats, and statistical formulas unchanged.')
    save(RUN/'design.json',design)
    save(ROOT/'active_campaign.json',dict(profile=PROFILE,run=str(RUN),runner=entry(ROOT/'fast2b_run.py'),activated_utc=utc()))
    update_status(state='ready',active_seconds=elapsed_active(ledger),no_automatic_day_restart=True)
    print(json.dumps(dict(frozen=True,profile=PROFILE,jobs=[j['id'] for j in jobs],active_seconds=elapsed_active(ledger))))


class GroupExecutor:
    """Scientific query timing ends before this class writes any checkpoint."""
    def __init__(self,out,segment,job,queries,truth,readers,process,design_hash,budget_check,fingerprint=s.fingerprint,pause_check=wanted_pause):
        self.out=Path(out);self.segment=Path(segment);self.job=job;self.qmap={q['id']:q for q in queries}
        self.truth=truth;self.readers=readers;self.process=process;self.design_hash=design_hash
        self.budget_check=budget_check;self.fingerprint=fingerprint;self.pause_check=pause_check

    def group(self,group,kind,ordinal):
        q=self.qmap[group['query']];records=[]
        directory=self.segment/('recovery' if kind=='recovery' else 'scheduled')
        directory.mkdir(parents=True,exist_ok=True)
        inflight=directory/f'g{ordinal:06d}.partial.jsonl'
        with inflight.open('x',encoding='utf-8') as stream:
            for index,method in enumerate(group['methods']):
                self.budget_check()
                begin=utc();at=time.perf_counter()
                actual,stats=self.readers[method].query(q,'ordered_dense')
                end=utc();rows=len(actual['rid']);digest=self.fingerprint(actual);del actual
                assert rows==self.truth[q['id']]['rows'] and digest==self.truth[q['id']]['sha256'],(method,q['id'])
                record=dict(dataset=self.job['dataset'],block=self.job['block'],query=q['id'],family=q['family'],method=method,
                            repeat=group['repeat'],warmup=group['repeat']<0 or kind=='recovery',contract='ordered_dense',cpu=1,io=4,
                            rows=rows,sha256=digest,started_utc=begin,ended_utc=end,time_policy=POLICY,
                            call_order=group['index']*7+index,record_kind=kind,segment_id=self.segment.name,
                            process=self.process,design_sha256=self.design_hash,call_with_validation_seconds=time.perf_counter()-at,**stats)
                self.budget_check()
                stream.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n');stream.flush()
                records.append(record)
            os.fsync(stream.fileno())
        target=directory/f'g{ordinal:06d}.json'
        save(target,dict(kind=kind,group=group,records=records))
        return entry(target)

    def scheduled(self,groups,cp,progress):
        base=cp['scheduled_seconds'];started=time.perf_counter();phase_start=utc();prior_repeat=None
        for index in range(cp['next_group'],len(groups)):
            if self.pause_check(): return cp,True
            group=groups[index]
            if group['repeat']!=prior_repeat:gc.collect();prior_repeat=group['repeat']
            progress('scheduled',phase_start,base,cp)
            commit=self.group(group,'scheduled',index)
            cp=dict(next_group=index+1,scheduled_seconds=base+time.perf_counter()-started,commits=cp['commits']+[commit])
            save(self.out/'checkpoint.json',cp)
            progress('scheduled',phase_start,base,cp)
            print(self.job['id'],'group',index+1,'/',len(groups),'repeat',group['repeat'],group['query'],flush=True)
        return cp,False


def worker(job_id):
    deny_network();design=verify_frozen();job=next(j for j in design['jobs'] if j['id']==job_id)
    out=RUN/'runs'/job_id;out.mkdir(parents=True,exist_ok=True)
    groups=load(design['schedules'][job_id]['path']);cp=validate_checkpoint(out,groups)
    assert cp['next_group']<=len(groups)
    segments=out/'segments';segments.mkdir(exist_ok=True)
    number=1+max([int(name[4:]) for name in segment_paths(out,design)]+[0])
    segment=segments/f'seg_{number:04d}';segment.mkdir()
    process=process_identity();started=utc()
    save(segment/'started.json',dict(started_utc=started,process=process,next_group=cp['next_group'],design_sha256=sha(RUN/'design.json')))
    phase=dict(name='initializing',started_utc=started,base=cp['scheduled_seconds'])
    def budget_check():
        measurement_budget_check(design,load(RUN/'budget.json'))
    def progress(name,start,base,checkpoint_value):
        phase.update(name=name,started_utc=start,base=base)
        save(out/'progress.json',dict(utc=utc(),phase=name,phase_started_utc=start,phase_base_seconds=base,
             next_group=checkpoint_value['next_group'],total_groups=len(groups),segment_id=segment.name,process=process,
             formal_calls=max(0,checkpoint_value['next_group']-216)*7,warmup_calls=min(216,checkpoint_value['next_group'])*7))
    result_code=0
    try:
        budget_check()
        if wanted_pause():result_code=SOFT_PAUSE;return result_code
        import campaign
        campaign.verify_assets(job['dataset'])
        from reader import Reader,configure_pools
        from georinex_reader import GeoRinexReader
        import pyarrow as pa
        m,queries,truth,paths=inputs(job['dataset']);configure_pools(1,4)
        readers={method:Reader(Path(paths[method]),m,cpu=1) for method in CORE}
        readers.update(GeoRinex_R=GeoRinexReader(m),GeoRinex_H=GeoRinexReader(m,compressed=True))
        save(segment/'environment.json',dict(python=sys.version,versions=campaign.PINNED,platform=platform.platform(),process=process,
             cpu_pool=pa.cpu_count(),io_pool=pa.io_thread_count(),thread_env={k:os.environ.get(k) for k in ENV_THREADS},
             reader_init_seconds={name:reader.init_seconds for name,reader in readers.items()},offline_enforced=True))
        engine=GroupExecutor(out,segment,job,queries,truth,readers,process,sha(RUN/'design.json'),budget_check)
        if cp['next_group']>0:
            recovery=recovery_schedule(groups,cp['next_group']);at=utc()
            save(segment/'recovery_plan.json',dict(groups=recovery,reason='fresh process after a paused or interrupted logical block'))
            for index,group in enumerate(recovery):
                if wanted_pause():result_code=SOFT_PAUSE;return result_code
                progress('recovery',at,cp['scheduled_seconds'],cp)
                engine.group(group,'recovery',index)
            save(segment/'recovery_complete.json',dict(utc=utc(),groups=len(recovery),calls=len(recovery)*7))
        cp,paused=engine.scheduled(groups,cp,progress)
        if paused:result_code=SOFT_PAUSE;return result_code
        assert cp['next_group']==864
        verify_frozen();campaign.verify_assets(job['dataset']);budget_check()
        rows=[r for c in cp['commits'] for r in load(c['path'])['records']]
        assert len(rows)==6048 and sum(not r['warmup'] for r in rows)==4536
        save(out/'verification.json',dict(all_passed=True,formal_calls=4536,warmup_calls=1512,dataset=job['dataset'],block=job['block'],
             time_policy=POLICY,design=entry(RUN/'design.json'),checkpoint=entry(out/'checkpoint.json'),ended_utc=utc(),
             scheduled_seconds=cp['scheduled_seconds'],segments=number,core_code=design['original_runtime_code']))
        return 0
    except BaseException:
        save(segment/'failure.json',dict(utc=utc(),traceback=traceback.format_exc()));result_code=1;raise
    finally:
        save(segment/'finished.json',dict(started_utc=started,ended_utc=utc(),process=process,returncode=result_code,
             paused=result_code==SOFT_PAUSE,next_group=checkpoint(out)['next_group']))


def pause_wait(design):
    ledger=load(RUN/'budget.json')
    if not wanted_pause(RUN):
        if ledger['pauses'] and not ledger['pauses'][-1].get('resumed_utc'):
            ledger['pauses'][-1].update(resumed_utc=utc(),resume_command=load(RUN/'control.json'))
            save(RUN/'budget.json',ledger)
        return
    if not ledger['pauses'] or ledger['pauses'][-1].get('resumed_utc'):
        ledger['pauses'].append(dict(paused_utc=utc(),pause_command=load(RUN/'control.json')));save(RUN/'budget.json',ledger)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    update_status(state='paused',active_seconds=elapsed_active(ledger),pause=ledger['pauses'][-1],worker_exited=True)
    while wanted_pause(RUN):time.sleep(1)
    ledger=load(RUN/'budget.json');ledger['pauses'][-1].update(resumed_utc=utc(),resume_command=load(RUN/'control.json'))
    save(RUN/'budget.json',ledger)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    verify_frozen()


def kill_tree(child):
    if child is None or child.poll() is not None:return
    try:
        p=psutil.Process(child.pid);processes=p.children(recursive=True)+[p]
        for item in processes:
            try:item.kill()
            except psutil.NoSuchProcess:pass
        psutil.wait_procs(processes,timeout=5)
    except psutil.NoSuchProcess:pass
    child.wait(timeout=10)


def stop_owned_worker(child,reason,job_id=None):
    """Record controller-observed termination separately from worker completion."""
    kill_tree(child)
    if child is None or not job_id:return
    out=RUN/'runs'/job_id;segments=sorted((out/'segments').glob('seg_*'))
    if not segments:return
    seg=segments[-1]
    if (seg/'finished.json').exists() or (seg/'supervisor_termination.json').exists():return
    if not (seg/'environment.json').exists():return
    start=load(seg/'started.json');env=load(seg/'environment.json');assert not alive(env['process'])
    event=dict(utc=utc(),observer=process_identity(),process=env['process'],worker_exited=True,
               reason=reason,active_seconds=elapsed_active(load(RUN/'budget.json')))
    event_path=RUN/'terminations'/(job_id+'_'+seg.name+'.json');save(event_path,event)
    save(seg/'supervisor_termination.json',dict(kind='controller_observed_termination',retrospective=False,
         recorded_utc=utc(),started_utc=start['started_utc'],ended_utc=event['utc'],process=env['process'],
         observer=event['observer'],worker_exited=True,returncode=child.returncode if child else None,reason=reason,
         evidence=[entry(event_path),entry(seg/'started.json'),entry(seg/'environment.json')]))


def interruption_report(problem):
    design=load(RUN/'design.json');rows=[];checked_formal=4536;checked_warm=1512
    for job in design['jobs']:
        out=RUN/'runs'/job['id']
        try:
            cp=validate_checkpoint(out,load(RUN/'schedules'/(job['id']+'.json')))
            formal=max(0,cp['next_group']-216)*7;warm=min(216,cp['next_group'])*7
            certified=(out/'verification.json').exists()
            if certified:
                cert=load(out/'verification.json');assert cert['all_passed'];check_entry(cert['checkpoint'])
            rows.append(dict(job=job['id'],committed_groups=cp['next_group'],formal=formal,warmup=warm,
                             complete_block=certified,checkpoint_hashes_passed=True))
            checked_formal+=formal;checked_warm+=warm
        except Exception as error:
            rows.append(dict(job=job['id'],checkpoint_hashes_passed=False,error=str(error)))
    save(RUN/'interrupted_coverage.json',dict(utc=utc(),complete=False,checked_formal_calls=checked_formal,
         checked_planned_warmup_calls=checked_warm,prior_builds=204,jobs=rows,problem=problem))
    lines=[f"| {r['job']} | {r.get('committed_groups','待复核')} | {r.get('formal','待复核')} | {r.get('warmup','待复核')} | {'完整' if r.get('complete_block') else '未完成'} |" for r in rows]
    text='''# EXP-028本轮停止记录

测量或验收在全部交付完成前停止。已提交请求组及未提交现场保留；没有删减慢请求，也未把不完整区组汇总成两区组结果。

| 追加任务 | 已提交请求组/864 | 正式调用 | 计划预热 | 区组状态 |
|---|---:|---:|---:|---|
'''+ '\n'.join(lines)+f'''

此前D1第一区组的4,536次正式调用、1,512次计划预热和全部204次构建继续保留。本轮含原D1在内，已核对检查点哈希的正式调用为{checked_formal:,}次，计划预热为{checked_warm:,}次；该计数不等于完整两区组统计已通过。

停止时累计有效时间为{problem['active_seconds']/3600:.3f}小时。详细缺口见`interrupted_coverage.json`；异常信息如下。等待后续聊天指令，不自行启动下一次尝试。

```
{problem['traceback']}
```
'''
    (RUN/'未完成说明.md').write_text(text,encoding='utf-8')


def supervise():
    deny_network()
    with lock(RUN/'supervisor.lock'):
        design=verify_frozen();identity=process_identity();save(RUN/'controller.json',dict(**identity,started_utc=utc()))
        child=None;active_job=None
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        try:
            for job in design['jobs']:
                active_job=job['id'];out=RUN/'runs'/active_job
                if (out/'verification.json').exists():
                    cert=load(out/'verification.json');assert cert['all_passed'];check_entry(cert['checkpoint']);continue
                while not (out/'verification.json').exists():
                    pause_wait(design)
                    assert elapsed_active(load(RUN/'budget.json'))<design['measurement_budget_seconds'],'Measurement budget exhausted'
                    battery=psutil.sensors_battery();assert battery is None or battery.power_plugged,'AC power required'
                    verify_frozen()
                    log=RUN/'logs'/(active_job+'_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.log');log.parent.mkdir(exist_ok=True)
                    with log.open('x',encoding='utf-8') as output:
                        child=subprocess.Popen([sys.executable,'-X','utf8','-B',str(ROOT/'fast2b_run.py'),'worker','--job',active_job],
                             cwd=str(ROOT),env={**os.environ,**ENV_THREADS},stdout=output,stderr=subprocess.STDOUT)
                        last=0.
                        while child.poll() is None:
                            seconds=elapsed_active(load(RUN/'budget.json'))
                            if seconds>=design['measurement_budget_seconds']:
                                stop_owned_worker(child,'Extended overall measurement budget exhausted',active_job)
                                raise TimeoutError('Extended overall measurement budget exhausted')
                            progress=load(out/'progress.json') if (out/'progress.json').exists() else {}
                            if time.monotonic()-last>=5:
                                update_status(state='pause_requested' if wanted_pause() else 'running',job=job,worker_pid=child.pid,
                                     progress=progress,active_seconds=seconds,measurement_seconds_left=design['measurement_budget_seconds']-seconds,
                                     total_seconds_left=design['active_budget_seconds']-seconds,controller=identity)
                                last=time.monotonic()
                            time.sleep(1)
                    code=child.returncode
                    if code not in [0,SOFT_PAUSE]:stop_owned_worker(child,'Unexpected worker exit',active_job)
                    child=None
                    if code==SOFT_PAUSE:pause_wait(design);continue
                    assert code==0,f'Worker failed ({code}); retained in {out}'
            active_job=None
            for stage,script in [('analysis','fast2b_analysis.py'),('independent','fast2b_verify.py'),('report','fast2b_report.py')]:
                receipt=RUN/'pipeline'/(stage+'.json')
                if receipt.exists():
                    assert load(receipt)['all_passed'];continue
                pause_wait(design);verify_frozen()
                assert elapsed_active(load(RUN/'budget.json'))<design['active_budget_seconds'],'Extended total active budget'
                log=RUN/'logs'/(stage+'.log')
                with log.open('a',encoding='utf-8') as output:
                    child=subprocess.Popen([sys.executable,'-X','utf8','-B',str(ROOT/script),'--source-manifest',str(RUN/'source_manifest.json'),
                         '--output',str(RUN/'analysis')],cwd=str(ROOT),stdout=output,stderr=subprocess.STDOUT,env={**os.environ,**ENV_THREADS})
                    while child.poll() is None:
                        seconds=elapsed_active(load(RUN/'budget.json'))
                        if seconds>=design['active_budget_seconds']:
                            stop_owned_worker(child,'Extended total active budget exhausted')
                            raise TimeoutError('Extended total active budget exhausted')
                        update_status(state='pause_requested' if wanted_pause() else 'analyzing',stage=stage,active_seconds=seconds,worker_pid=child.pid)
                        time.sleep(2)
                assert child.returncode==0,f'Pipeline stage failed: {stage}'
                child=None;save(receipt,dict(all_passed=True,utc=utc(),script=entry(ROOT/script)))
            update_status(state='complete',active_seconds=elapsed_active(load(RUN/'budget.json')),
                          report=str(RUN/'analysis/结果报告.md'),formal_calls=18144,warmup_calls=6048,layout_builds=204)
            save(RUN/'complete.json',dict(all_passed=True,utc=utc(),independent=entry(RUN/'analysis/independent_verification.json'),
                 report=entry(RUN/'analysis/结果报告.md')))
        except BaseException:
            stop_owned_worker(child,'Supervisor stopped after an exception',active_job)
            problem=dict(utc=utc(),job=active_job,traceback=traceback.format_exc(),active_seconds=elapsed_active(load(RUN/'budget.json')))
            save(RUN/'failures'/(dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json'),problem)
            update_status(state='interrupted',**{k:v for k,v in problem.items() if k!='utc'},worker_exited=True)
            interruption_report(problem)
            raise
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            save(RUN/'controller_finished.json',dict(utc=utc(),process=identity,child_exited=child is None or child.poll() is not None))


def launch():
    with lock(RUN/'launch.lock'):
        if (RUN/'controller.json').exists() and alive(load(RUN/'controller.json')):return 'already_running'
        if (RUN/'launcher.json').exists() and alive(load(RUN/'launcher.json')):return 'already_starting'
        verify_frozen()
        stamp=dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f');logs=RUN/'logs';logs.mkdir(exist_ok=True)
        with (logs/('supervisor_'+stamp+'.stdout.log')).open('x',encoding='utf-8') as stdout,(logs/('supervisor_'+stamp+'.stderr.log')).open('x',encoding='utf-8') as stderr:
            p=subprocess.Popen([sys.executable,'-X','utf8','-B',str(ROOT/'fast2b_run.py'),'supervise'],cwd=str(ROOT),
                 env={**os.environ,**ENV_THREADS},stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
        process=psutil.Process(p.pid);save(RUN/'launcher.json',dict(pid=p.pid,created=process.create_time(),utc=utc()))
        return 'started'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','start','supervise','worker','status','pause','resume']);parser.add_argument('--job')
    args=parser.parse_args()
    if args.command=='freeze':freeze()
    elif args.command=='start':print(launch())
    elif args.command=='supervise':supervise()
    elif args.command=='worker':sys.exit(worker(args.job))
    elif args.command=='status':
        state=load(RUN/'status.json');ledger=load(RUN/'budget.json')
        state.update(active_seconds=elapsed_active(ledger),controller_alive=alive(load(RUN/'controller.json')) if (RUN/'controller.json').exists() else False,
                     control=load(RUN/'control.json'),pauses=ledger['pauses'])
        print(json.dumps(state,ensure_ascii=False,indent=2))
    elif args.command=='pause':
        print(json.dumps(command('paused','用户在聊天中要求暂停实验'),ensure_ascii=False))
    else:
        state=load(RUN/'status.json')
        if state['state']=='complete':print('already_complete');return
        verify_frozen()
        for job in load(RUN/'design.json')['jobs']:
            out=RUN/'runs'/job['id']
            if out.exists():validate_checkpoint(out,load(RUN/'schedules'/(job['id']+'.json')))
        print(json.dumps(command('running','用户在聊天中要求恢复实验'),ensure_ascii=False));print(launch())


if __name__=='__main__':main()
