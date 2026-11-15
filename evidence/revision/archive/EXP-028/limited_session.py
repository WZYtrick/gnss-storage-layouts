"""One user-authorized hour of complete construction blocks; no automatic continuation."""
from common import *
import argparse
import ctypes
import psutil
import subprocess
import time
import traceback
from datetime import datetime,timedelta,timezone
import campaign

RUN='revision_v1_20261112'
SESSION='construction_one_hour_20261113'
POLICY='explicit_user_one_hour_20261113'

def session_root():return ROOT/'sessions'/SESSION
def process_identity():
    p=psutil.Process();return dict(pid=p.pid,created=p.create_time(),executable=p.exe())
def authorized(design):
    now=datetime.now(timezone.utc)
    return datetime.fromisoformat(design['started_utc'])<=now<datetime.fromisoformat(design['deadline_utc'])
def remaining(design):return max(0.,(datetime.fromisoformat(design['deadline_utc'])-datetime.now(timezone.utc)).total_seconds())
def check(design):
    original=load(ROOT/'campaigns'/RUN/'design.json')
    assert sha(ROOT/'campaigns'/RUN/'design.json')==design['original_design_sha256']
    assert sha(ROOT/'limited_session.py')==design['wrapper_sha256']
    campaign.guard(original)
    assert (ROOT/'STOP_AFTER_BLOCK').exists(),'The full-night supervisor must remain stopped'
    return original

def worker(job_id):
    folder=session_root();design=load(folder/'authorization.json');original=check(design)
    assert authorized(design)
    job=next(j for j in design['jobs'] if j['id']==job_id)
    out=ROOT/'campaigns'/RUN/'runs'/job_id
    identity=process_identity();started=utc()
    assert job['kind']=='construction' and not out.exists()
    import construction
    # Only authorization checks and out-of-timer provenance are supplied by
    # this wrapper. The frozen preparation, encoding, initialization, and
    # content-validation implementation is used without edits.
    construction.in_window=lambda:authorized(design)
    original_dump=construction.dump
    def annotated_dump(path,value):
        value=dict(value)
        if Path(path).name=='verification.json':
            assert authorized(design),'Completed outside the authorized hour'
            value.update(time_policy=POLICY,execution_session=SESSION,
                         authorization=entry(folder/'authorization.json'),
                         session_wrapper=entry(ROOT/'limited_session.py'),
                         started_utc=started,process=identity)
        elif Path(path).name=='status.json':
            value.update(execution_session=SESSION,time_policy=POLICY)
        original_dump(path,value)
    construction.dump=annotated_dump
    try:
        campaign.verify_assets(job['dataset'])
        construction.measure(job['dataset'],job['block'],out,smoke=False)
        check(design);campaign.verify_assets(job['dataset'])
        assert authorized(design)
        dump(out/'session_execution.json',dict(all_passed=True,session=SESSION,job=job,
             started_utc=started,ended_utc=utc(),process=identity,time_policy=POLICY,
             authorization=entry(folder/'authorization.json')))
    except BaseException:
        dump(out/'failure.json',dict(utc=utc(),session=SESSION,traceback=traceback.format_exc()));raise

def run():
    folder=session_root();folder.mkdir(parents=True,exist_ok=False)
    root=ROOT/'campaigns'/RUN;original=load(root/'design.json');campaign.guard(original)
    lock=load(ROOT/'campaigns'/('supervisor_'+RUN+'.json'))
    assert 'finished_utc' in lock and (ROOT/'STOP_AFTER_BLOCK').exists()
    try:
        p=psutil.Process(lock['pid'])
        assert p.create_time()!=lock['created'],'Original supervisor is still active'
    except psutil.NoSuchProcess:pass
    battery=psutil.sensors_battery();assert battery is None or battery.power_plugged,'AC power required'
    jobs=[j for j in original['jobs'] if j['kind']=='construction' and not (root/'runs'/j['id']/'verification.json').exists()]
    assert jobs and all(not (root/'runs'/j['id']).exists() for j in jobs)
    start=datetime.now(timezone.utc);deadline=start+timedelta(hours=1)
    identity=process_identity()
    design=dict(session=SESSION,original_run=RUN,created=utc(),started_utc=start.isoformat(),deadline_utc=deadline.isoformat(),
        duration_seconds=3600,time_policy=POLICY,user_instruction='立即测量一小时，记录时段调整；然后汇报状态，推迟其他测量。',
        original_design_sha256=sha(root/'design.json'),wrapper_sha256=sha(ROOT/'limited_session.py'),
        unchanged_runtime_code=original['runtime_code'],process=identity,jobs=jobs,
        scheduling_rule='Keep the relative frozen order of construction jobs; start only a complete job whose reserved budget fits, with 30 seconds of closing reserve.',
        interpretation='Post-review formal construction measurements under this explicit one-hour authorization. Original 01:00-08:00 authorization and job order are superseded only for this session. Baseline jobs remain deferred.',
        analysis_requirement='A versioned analysis must validate this authorization before including these blocks; the old analyzer intentionally rejects the new time-policy label.')
    dump(folder/'authorization.json',design)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    completed=[];active=None;stop_reason=None
    try:
        for job in jobs:
            if remaining(design)<job['scheduling_seconds']+30:
                stop_reason='Next complete block does not fit the remaining hour';break
            check(design)
            log=folder/(job['id']+'.log');started=time.monotonic()
            with log.open('x',encoding='utf-8') as output:
                active=subprocess.Popen([sys.executable,'-X','utf8','-B',str(ROOT/'limited_session.py'),'worker','--job',job['id']],
                    cwd=str(ROOT),env={**os.environ,**ENV_THREADS},stdout=output,stderr=subprocess.STDOUT)
                while active.poll() is None:
                    status=dict(state='running_limited_session',session=SESSION,utc=utc(),process=identity,
                                worker_pid=active.pid,job=job,elapsed_seconds=time.monotonic()-started,
                                seconds_remaining=remaining(design),deadline_utc=design['deadline_utc'],completed=completed)
                    dump(folder/'status.json',status);dump(root/'status.json',status)
                    if remaining(design)<=0:
                        process=psutil.Process(active.pid)
                        for child in process.children(recursive=True):child.terminate()
                        process.terminate();active.wait(timeout=30)
                        dump(root/'runs'/job['id']/'failure.json',dict(utc=utc(),session=SESSION,
                             reason='User-authorized one-hour deadline; incomplete block preserved and excluded'))
                        stop_reason='One-hour deadline reached during incomplete block';break
                    time.sleep(2)
                if active.returncode!=0:
                    if stop_reason:break
                    raise RuntimeError('Construction worker failed; retained for review: '+job['id'])
            cert=load(root/'runs'/job['id']/'verification.json')
            execution=load(root/'runs'/job['id']/'session_execution.json')
            assert cert['all_passed'] and execution['all_passed'] and cert['time_policy']==POLICY
            completed.append(dict(job=job['id'],dataset=job['dataset'],block=job['block'],
                                  layouts=len(cert['results']),elapsed_seconds=time.monotonic()-started,
                                  verification=entry(root/'runs'/job['id']/'verification.json')))
            active=None
        result=dict(state='stopped_after_limited_session',utc=utc(),session=SESSION,
            started_utc=design['started_utc'],deadline_utc=design['deadline_utc'],
            actual_elapsed_seconds=(datetime.now(timezone.utc)-start).total_seconds(),
            completed=completed,layout_builds=sum(r['layouts'] for r in completed),
            stop_reason=stop_reason or 'All pending construction blocks completed',
            remaining_measurements='Deferred until a new user instruction; STOP_AFTER_BLOCK retained',
            authorization=entry(folder/'authorization.json'),process=identity)
        dump(folder/'summary.json',result);dump(folder/'status.json',result);dump(root/'status.json',result)
    except BaseException:
        dump(folder/'failure.json',dict(utc=utc(),session=SESSION,traceback=traceback.format_exc()));raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        dump(folder/'finished.json',dict(utc=utc(),process=identity))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['run','worker']);p.add_argument('--job');a=p.parse_args()
    if a.command=='run':run()
    else:worker(a.job)
