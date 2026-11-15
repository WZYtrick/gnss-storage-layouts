"""Synthetic execution and independent-analysis acceptance tests; never formal data."""
from fast2_support import *
from fast2_run import GroupExecutor,kill_tree
import fast2_run
import fast2_analysis as primary
import fast2_verify as independent
import fast2_report as report
import copy
import subprocess
import traceback
import io
import contextlib


def test_engine(base):
    queries=[dict(id='q1',family='Q1'),dict(id='q2',family='Q2')]
    truth={q['id']:dict(rows=1,sha256='fixture') for q in queries}
    groups=schedule(queries,0);job=dict(dataset='FIXTURE',block=0,id='fixture')
    def progress(*args):pass
    class Fake:
        def __init__(self,state):self.state=state
        def query(self,q,contract):
            self.state['calls']+=1
            if self.state.get('fail_at')==self.state['calls']:raise RuntimeError('deliberate uncommitted group interruption')
            if self.state.get('pause_at')==self.state['calls']:self.state['paused']=True
            return dict(rid=[1]),dict(seconds=1.,scan_seconds=.4,normalize_seconds=.6)
    def engine(out,seg,state):
        segment=out/seg;segment.mkdir(parents=True)
        return GroupExecutor(out,segment,job,queries,truth,{m:Fake(state) for m in METHODS},dict(pid=0,created=0),
             'fixture',lambda:None,fingerprint=lambda a:'fixture',pause_check=lambda:state['paused'])
    for point in range(1,8):
        out=base/f'pause_at_{point}';state=dict(calls=0,paused=False,pause_at=point)
        cp,paused=engine(out,'seg_0001',state).scheduled(groups,checkpoint(out),progress)
        assert paused and cp['next_group']==1 and state['calls']==7
        state.update(paused=False,pause_at=None)
        resumed=engine(out,'seg_0002',state)
        for index,g in enumerate(recovery_schedule(groups,cp['next_group'])):
            recovery=resumed.group(g,'recovery',index)
            assert all(r['warmup'] and r['record_kind']=='recovery' for r in load(recovery['path'])['records'])
        assert checkpoint(out)['next_group']==1 and state['calls']==21
        cp,paused=resumed.scheduled(groups,validate_checkpoint(out,groups),progress)
        assert not paused and cp['next_group']==8 and state['calls']==70
        rows=[r for item in cp['commits'] for r in load(item['path'])['records']]
        assert [(r['query'],r['method'],r['repeat']) for r in rows]==[(g['query'],m,g['repeat']) for g in groups for m in g['methods']]
        assert len({(r['query'],r['method'],r['repeat']) for r in rows})==56
        assert recovery_schedule(groups,7)==[g for g in groups if g['repeat']==-1 and g['query']==groups[7]['query']]
    out=base/'crash';state=dict(calls=0,paused=False,fail_at=4)
    try:engine(out,'seg_0001',state).scheduled(groups,checkpoint(out),progress);raise AssertionError('Crash was not injected')
    except RuntimeError as error:assert 'deliberate' in str(error)
    assert checkpoint(out)['next_group']==0
    partial=list((out/'seg_0001/scheduled').glob('*.partial.jsonl'));assert len(partial)==1 and len(partial[0].read_text().splitlines())==3
    state.update(fail_at=None)
    cp,paused=engine(out,'seg_0002',state).scheduled(groups,checkpoint(out),progress)
    assert cp['next_group']==8 and len(cp['commits'])==8
    save(out/'schedule.json',groups)
    process=subprocess.run([sys.executable,'-X','utf8','-B','-c',
         'import sys; from fast2_support import *; print(validate_checkpoint(Path(sys.argv[1]),load(Path(sys.argv[1])/"schedule.json"))["next_group"])',str(out)],
         cwd=ROOT,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    assert process.returncode==0 and process.stdout.strip()=='8',process.stderr
    saved=copy.deepcopy(cp);cp['next_group']=7;save(out/'checkpoint.json',cp)
    try:validate_checkpoint(out,groups);raise AssertionError('Wrong checkpoint accepted')
    except AssertionError as error:
        assert str(error)!='Wrong checkpoint accepted'
    save(out/'checkpoint.json',saved)
    first=Path(saved['commits'][0]['path']);old=first.read_bytes();first.write_bytes(old+b' ')
    try:validate_checkpoint(out,groups);raise AssertionError('Changed group hash accepted')
    except AssertionError as error:assert str(error)!='Changed group hash accepted'
    first.write_bytes(old)
    return 11


def test_control(base):
    base.mkdir(parents=True)
    save(base/'control.json',dict(desired='running',revision=0,received_utc=utc()))
    assert command('paused','test pause',base)['revision']==1
    assert command('paused','duplicate pause',base)['revision']==1
    assert command('running','test resume',base)['revision']==2
    assert command('running','duplicate resume',base)['revision']==2
    zero=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
    ledger=dict(started_utc=zero.isoformat(),pauses=[dict(paused_utc=(zero+dt.timedelta(seconds=300)).isoformat(),resumed_utc=(zero+dt.timedelta(seconds=600)).isoformat())])
    assert elapsed_active(ledger,zero+dt.timedelta(seconds=900))==600
    assert elapsed_active(ledger,zero+dt.timedelta(seconds=100))==100
    ledger['pauses'][0]['resumed_utc']=None
    assert elapsed_active(ledger,zero+dt.timedelta(seconds=900))==300
    with lock(base/'exclusive.lock'):
        try:
            with lock(base/'exclusive.lock'):pass
            raise AssertionError('Duplicate supervisor lock accepted')
        except OSError:pass
    old_run=fast2_run.RUN;fast2_run.RUN=base
    try:
        save(base/'budget.json',ledger)
        fast2_run.pause_wait({})
        assert load(base/'budget.json')['pauses'][-1]['resumed_utc']
    finally:fast2_run.RUN=old_run
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],creationflags=subprocess.CREATE_NO_WINDOW)
    kill_tree(child);assert child.poll() is not None
    return 10


def fixture(base):
    base.mkdir(parents=True);baselines=[]
    for ds in ['D1','D2']:
        _,queries,truth,_=inputs(ds)
        for block in [0,1]:
            out=base/f'baseline_{ds}_b{block:02d}';out.mkdir()
            rows=[];clock=dt.datetime(2020,1,1,tzinfo=dt.timezone.utc)
            for g in schedule(queries,block):
                q=next(q for q in queries if q['id']==g['query']);family=int(q['family'][1:])
                for method in g['methods']:
                    factor=(METHODS.index(method)+1)*.001 if method in CORE else ([1.,.5,.3,20.,60.,.7][family-1]+(method=='GeoRinex_H')*.3)
                    if q['family']=='Q6' and method=='F':factor=1.5
                    seconds=factor*(1+block*.03)*(1+g['repeat']*.02)
                    start=clock;clock+=dt.timedelta(seconds=seconds+.01)
                    stats=dict(seconds=seconds,scan_seconds=seconds*.7,normalize_seconds=seconds*.3)
                    if method.startswith('GeoRinex'):
                        stats.update(decompress_seconds=seconds*.1,parser_seconds=seconds*.6,adapter_seconds=seconds*.3,overhead_seconds=0.)
                    rows.append(dict(dataset=ds,block=block,query=q['id'],family=q['family'],method=method,repeat=g['repeat'],warmup=g['repeat']<0,
                         contract='ordered_dense',cpu=1,io=4,rows=truth[q['id']]['rows'],sha256=truth[q['id']]['sha256'],
                         started_utc=start.isoformat(),ended_utc=clock.isoformat(),call_order=len(rows),process=dict(pid=0,created=0),time_policy='SYNTHETIC_FIXTURE',**stats))
            with (out/'timings.jsonl').open('w',encoding='utf-8') as f:
                for row in rows:f.write(json.dumps(row)+'\n')
            save(out/'verification.json',dict(all_passed=True,purpose='fixture',formal_calls=4536,warmup_calls=1512))
            baselines.append(dict(id=out.name,dataset=ds,block=block,origin='fixture',path=str(out)))
    old=load(OLD/'design.json')
    manifest=dict(purpose='fixture',query_blocks=[0,1],construction_blocks=list(range(6)),baselines=baselines,
           constructions=[dict(dataset=j['dataset'],block=j['block'],path=str(OLD/'runs'/j['id'])) for j in old['jobs'] if j['kind']=='construction'])
    save(base/'source_manifest.json',manifest)
    return base/'source_manifest.json'


def test_analysis(base):
    manifest=fixture(base);output=base/'analysis'
    assert primary.break_even(10,2,1)==10 and independent.n_queries(10,2,1)==10
    assert primary.break_even(10,1,1) is None and primary.break_even(10,.5,1) is None
    assert primary.break_even(10.1,2,1)==11
    assert sorted([dict(seconds=1,repeat=2),dict(seconds=1,repeat=0),dict(seconds=1,repeat=1)],key=lambda r:(r['seconds'],r['repeat']))[1]['repeat']==1
    try:primary.collect(manifest,False);raise AssertionError('Fixture accepted as formal')
    except AssertionError as error:assert str(error)!='Fixture accepted as formal'
    primary.analyze(manifest,output,True);independent.verify(manifest,output,True)
    victim=output/'query_summary.csv';original=victim.read_bytes();proof_path=output/'verification.json';proof_original=proof_path.read_bytes()
    rows=primary.load(output/'verification.json')
    import csv,io
    parsed=list(csv.DictReader(io.StringIO(victim.read_text(encoding='utf-8'))));parsed[0]['A_ms_geo']=str(float(parsed[0]['A_ms_geo'])+1)
    primary.csvout(victim,parsed);rows['output_hashes'][victim.name]=sha(victim);save(proof_path,rows)
    try:independent.verify(manifest,output,True);raise AssertionError('Incorrect statistic accepted')
    except AssertionError as error:assert str(error)!='Incorrect statistic accepted'
    victim.write_bytes(original);proof_path.write_bytes(proof_original)
    raw=Path(load(manifest)['baselines'][0]['path'])/'timings.jsonl';raw_original=raw.read_bytes();raw.write_bytes(b'\n'.join(raw_original.splitlines()[:-1])+b'\n')
    try:primary.collect(manifest,True);raise AssertionError('Missing call accepted')
    except AssertionError as error:assert str(error)!='Missing call accepted'
    raw.write_bytes(raw_original)
    report.render(manifest,output,True)
    assert (output/'query_costs.png').exists() and (output/'construction_costs.png').exists()
    return 10,str(output)


def main():
    base=ROOT/'validation'/('fast2_fixture_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f'));base.mkdir(parents=True)
    started=utc()
    try:
        with contextlib.redirect_stdout(io.StringIO()):checks=test_engine(base/'engine')
        checks+=test_control(base/'control')
        deny_network()
        try:socket.create_connection(('127.0.0.1',9));raise AssertionError('Network not disabled')
        except RuntimeError as error:assert 'OFFLINE' in str(error)
        count,figures=test_analysis(base/'statistics');checks+=count+1
        guard=base/'guard';guard.mkdir();save(guard/'source_manifest.json',dict(purpose='fixture'))
        expected=dict(old_design=entry(OLD/'design.json'),fast_code={name:sha(ROOT/name) for name in FAST_FILES},schedules={},source_manifest=entry(guard/'source_manifest.json'))
        save(guard/'design.json',expected);verify_frozen(guard)
        expected['fast_code']['fast2_run.py']='0'*64;save(guard/'design.json',expected)
        try:verify_frozen(guard);raise AssertionError('Changed code accepted')
        except AssertionError as error:assert str(error)!='Changed code accepted'
        result=dict(all_passed=True,started_utc=started,ended_utc=utc(),checks=checks+2,purpose='synthetic acceptance tests, not measured evidence',
                    coverage=['pause at seven method positions','group completion before pause','disk checkpoint across fresh process',
                              'uncommitted partial group retained and replayed','duplicate controls and exclusive lock','paused time excluded; recovery time charged',
                              'historical authorization time arithmetic','network denied during full analysis and plotting',
                              'two query blocks plus six construction blocks','same-call median stages','zero and negative amortization denominator',
                              'corrupted statistic rejected independently','missing call rejected','frozen code mismatch rejected','owned-child timeout termination'],
                    fixture_directory=str(base),figure_directory=figures,fast_code={name:sha(ROOT/name) for name in FAST_FILES})
        save(ROOT/'validation/fast2_tests.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
    except BaseException:
        save(base/'failure.json',dict(utc=utc(),traceback=traceback.format_exc()));raise


if __name__=='__main__':main()
