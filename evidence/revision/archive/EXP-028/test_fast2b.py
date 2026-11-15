"""Acceptance tests for the extended budget and cross-version provenance."""
from fast2b_support import *
import fast2b_run as runner
import fast2b_analysis as primary
import fast2b_verify as independent
import fast2b_report as report
import test_fast2 as previous_tests
import ast
import copy
import io
import contextlib
import subprocess
import traceback


def rejects(fn):
    try:fn()
    except (AssertionError,FileNotFoundError):return
    raise AssertionError('Deliberate invalid evidence was accepted')


def provenance_tests(base):
    oldseg=base/'old/seg_0001';out=base/'runs/job';newseg=out/'segments/seg_0002'
    oldseg.mkdir(parents=True);newseg.mkdir(parents=True)
    identity=dict(pid=1,created=2.,executable='SYNTHETIC')
    olddesign=dict(time_policy='old-policy',measurement_budget_seconds=21*3600)
    save(base/'previous.json',olddesign);proof=base/'parent_observed.json';save(proof,dict(terminated=True))
    audit=dict(kind='controller_observed_termination',retrospective=True,started_utc='2026-01-01T01:00:00+00:00',
               ended_utc='2026-01-01T02:00:00+00:00',process=identity,worker_exited=True,evidence=[entry(proof)])
    save(base/'termination.json',audit)
    design=dict(previous_design=entry(base/'previous.json'),time_policy='new-policy',measurement_budget_seconds=32*3600,
                inherited_segments={'job':{'seg_0001':dict(path=str(oldseg),termination_audit=entry(base/'termination.json'))}})
    save(base/'design.json',design);ledger=dict(started_utc='2026-01-01T00:00:00+00:00',pauses=[])
    for seg,hour,hash_,policy in [(oldseg,1,sha(base/'previous.json'),'old-policy'),(newseg,3,sha(base/'design.json'),'new-policy')]:
        start=f'2026-01-01T0{hour}:00:00+00:00';end=f'2026-01-01T0{hour+1}:00:00+00:00'
        save(seg/'environment.json',dict(process=identity,cpu_pool=1,io_pool=4))
        save(seg/'started.json',dict(process=identity,started_utc=start,design_sha256=hash_))
        if seg==newseg:save(seg/'finished.json',dict(process=identity,started_utc=start,ended_utc=end))
        r=dict(process=identity,segment_id=seg.name,started_utc=f'2026-01-01T0{hour}:30:00+00:00',
               ended_utc=f'2026-01-01T0{hour}:30:01+00:00',design_sha256=hash_,time_policy=policy)
        for validate in [row_provenance,independent.validate_origin]:
            validate(r,out,design,base,ledger)
            for field,value in [('process',dict(pid=3)),('design_sha256','0'*64),('time_policy','wrong')]:
                wrong=copy.deepcopy(r);wrong[field]=value;rejects(lambda:validate(wrong,out,design,base,ledger))
    content=proof.read_bytes();proof.write_bytes(content+b' ')
    r=dict(process=identity,segment_id=oldseg.name,started_utc='2026-01-01T01:30:00+00:00',
           ended_utc='2026-01-01T01:30:01+00:00',design_sha256=sha(base/'previous.json'),time_policy='old-policy')
    rejects(lambda:row_provenance(r,out,design,base,ledger));rejects(lambda:independent.validate_origin(r,out,design,base,ledger));proof.write_bytes(content)
    return 18


def termination_test(base):
    job='owned_child';seg=base/'runs'/job/'segments/seg_0001';seg.mkdir(parents=True)
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],creationflags=subprocess.CREATE_NO_WINDOW)
    proc=psutil.Process(child.pid);identity=dict(pid=proc.pid,created=proc.create_time(),executable=proc.exe())
    save(seg/'environment.json',dict(process=identity));save(seg/'started.json',dict(process=identity,started_utc=utc()))
    save(base/'budget.json',dict(started_utc=utc(),pauses=[]));old_run=runner.RUN;runner.RUN=base
    try:
        runner.stop_owned_worker(child,'Synthetic overall-budget expiration',job)
        assert child.poll() is not None and not alive(identity)
        assert not (seg/'finished.json').exists()
        finish=segment_finish(seg,base/'runs'/job,dict(inherited_segments={}))
        assert finish['worker_exited'] and not finish['retrospective']
        digest=sha(seg/'supervisor_termination.json');runner.stop_owned_worker(child,'Repeated check',job)
        assert sha(seg/'supervisor_termination.json')==digest
    finally:runner.RUN=old_run
    return 4


def main():
    started=utc();base=ROOT/'validation'/('fast2b_fixture_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f'));base.mkdir()
    try:
        for name in FAST_FILES:ast.parse((ROOT/name).read_text(encoding='utf-8'))
        def node(path,name):return next(n for n in ast.parse((ROOT/path).read_text(encoding='utf-8')).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name)
        assert ast.dump(node('fast2_run.py','GroupExecutor'))==ast.dump(node('fast2b_run.py','GroupExecutor'))
        assert ast.dump(node('fast2_analysis.py','summarize'))==ast.dump(node('fast2b_analysis.py','summarize'))
        previous_tests.GroupExecutor=runner.GroupExecutor;previous_tests.fast2_run=runner;previous_tests.kill_tree=runner.kill_tree
        previous_tests.primary=primary;previous_tests.independent=independent;previous_tests.report=report
        with contextlib.redirect_stdout(io.StringIO()):checks=previous_tests.test_engine(base/'engine')
        checks+=previous_tests.test_control(base/'control');deny_network()
        count,figures=previous_tests.test_analysis(base/'statistics');checks+=count
        checks+=provenance_tests(base/'provenance');checks+=termination_test(base/'termination')
        zero=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc);ledger=dict(started_utc=zero.isoformat(),pauses=[])
        d=dict(measurement_budget_seconds=32*3600,scheduled_block_limit=None,recovery_round_limit=None)
        for hour in [2.1,7.1,21.1,31.99]:measurement_budget_check(d,ledger,zero+dt.timedelta(hours=hour))
        rejects(lambda:measurement_budget_check(d,ledger,zero+dt.timedelta(hours=32)));checks+=5
        result=dict(all_passed=True,started_utc=started,ended_utc=utc(),checks=checks+2,purpose='Synthetic tests, not measured evidence',
            fixture_directory=str(base),figure_directory=figures,fast_code={name:sha(ROOT/name) for name in FAST_FILES},
            unchanged_timing_engine_ast=True,unchanged_statistics_ast=True,
            coverage=['seven-method pause boundaries','recovery does not advance formal checkpoint','duplicate controls and exclusive lock',
              'cross-version identities and policies','retrospective controller-observed termination','missing or corrupted proof rejected',
              'owned-worker kill records controller evidence without forging finished.json','old 2/7/21-hour boundaries no longer interrupt',
              'new total deadline still enforced','offline full statistics, independent recomputation and report'])
        save(ROOT/'validation/fast2b_tests.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
    except BaseException:
        save(base/'failure.json',dict(utc=utc(),traceback=traceback.format_exc()));raise


if __name__=='__main__':main()
