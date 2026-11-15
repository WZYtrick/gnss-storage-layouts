"""Read current validation and campaign state without starting a measurement."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
from collections import defaultdict
import json,hashlib,math
import psutil

ROOT=Path(__file__).resolve().parent
RUN='revision_v1_20261112'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    now=datetime.now(timezone.utc);beijing=now.astimezone(timezone(timedelta(hours=8)))
    names=['common.py','storage.py','reader.py','georinex_reader.py','validate_baseline.py']
    code={name:sha(ROOT/name) for name in names}
    version=hashlib.sha256(json.dumps(code,sort_keys=True).encode()).hexdigest()[:12]
    folder=ROOT/'validation'/('all_'+version)
    records=[]
    if folder.exists():
        for p in folder.glob('*.json'):
            if p.name in ['verification.json','failure.json']:continue
            record=load(p);assert record['all_passed'] and record['code']==code,p
            records.append(record)
    grouped=defaultdict(list)
    for r in records:grouped[(r['dataset'],r['method'])].append(r)
    groups=[dict(dataset=ds,method=method,checked=len(grouped[(ds,method)]),expected=216,
                 query_seconds=math.fsum(r['seconds'] for r in grouped[(ds,method)]))
            for ds in ['D1','D2'] for method in ['GeoRinex_R','GeoRinex_H']]
    lock=load(ROOT/'campaigns'/('supervisor_'+RUN+'.json'))
    try:
        proc=psutil.Process(lock['pid']);alive=proc.create_time()==lock['created'] and proc.is_running()
    except psutil.NoSuchProcess:alive=None
    root=ROOT/'campaigns'/RUN
    state=load(root/'status.json') if (root/'status.json').exists() else load(ROOT/'campaigns'/('pending_'+RUN+'.json'))
    formal=warm=builds=0;completed=[]
    for p in root.glob('runs/*/verification.json'):
        cert=load(p);assert cert['all_passed'],p
        completed.append(p.parent.name)
        if p.parent.name.startswith('baseline'):
            formal+=cert['formal_calls'];warm+=cert['warmup_calls']
        else:builds+=len(cert['results'])
    regression=load(ROOT/'validation/regression_tests.json')
    runtime_unchanged=all(sha(ROOT/name)==value for name,value in regression['runtime_code'].items())
    report=dict(utc=now.isoformat(),beijing=beijing.isoformat(),validation=dict(checked=len(records),expected=864,groups=groups,
                  passed_so_far=True,complete=(folder/'verification.json').exists(),failure=(folder/'failure.json').exists()),
                formal=dict(completed_jobs=completed,formal_calls=formal,expected_formal_calls=54432,
                            warmup_calls=warm,expected_warmups=18144,layout_builds=builds,expected_builds=204),
                supervisor=dict(alive=alive,pid=lock['pid'],state=state['state'],runtime_unchanged=runtime_unchanged,
                                process_visibility_note=None if alive is not None else 'Process is not visible from this execution namespace; verify from the host before declaring it stopped.',
                                pending_or_status_utc=state.get('updated_utc',state.get('utc'))),
                window='01:00-08:00 Asia/Shanghai',published=False)
    if (root/'design.json').exists():
        design=load(root/'design.json');report['frozen_budget']=design['budget']
        report['frozen_scheduling_hours']=sum(j['scheduling_seconds'] for j in design['jobs'])/3600
    elif all(x['checked']==216 for x in groups[:2]):
        d1=sum(x['query_seconds'] for x in groups[:2])
        report['provisional_budget']=dict(d1_complete_baseline_block_hours=(4*d1+240)/3600,
                    baseline_active_hours_if_D2_equals_D1=12*(4*d1+240)/3600,
                    total_planning_hours='60-70',night_windows=12,
                    note='Estimate from D1 correctness calls; D2 and construction still update the frozen budget.')
    (ROOT/'progress').mkdir(exist_ok=True)
    path=ROOT/'progress'/('status_'+beijing.strftime('%Y%m%d_%H%M%S')+'.json')
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    (ROOT/'progress/latest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
