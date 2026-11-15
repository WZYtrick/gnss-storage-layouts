"""Extended authorization, preserving original measurement and provenance."""
from common import *
from fast2_support import (save,lock,deny_network,process_identity,alive,elapsed_active,
                           schedule,recovery_schedule,check_entry,checkpoint,validate_checkpoint)
import datetime as dt
import psutil
import time

PROFILE='revision_fast2_extended_20261114'
RUN=ROOT/'campaigns'/PROFILE
PREVIOUS=ROOT/'campaigns/revision_fast2_20261113'
OLD=ROOT/'campaigns/revision_v1_20261112'
POLICY='explicit_user_fast2_extended_20261114'
FAST_FILES=['fast2b_support.py','fast2b_run.py','fast2b_analysis.py','fast2b_verify.py','fast2b_report.py','exp028_control.py']


def verify_frozen(run=RUN):
    import campaign
    design=load(run/'design.json');check_entry(design['old_design']);campaign.guard(load(design['old_design']['path']))
    for name,digest in design['fast_code'].items():assert sha(ROOT/name)==digest,name
    check_entry(design['previous_design']);previous=load(design['previous_design']['path'])
    for name,digest in previous['fast_code'].items():assert sha(ROOT/name)==digest,name
    for item in design['schedules'].values():check_entry(item)
    check_entry(design['source_manifest'])
    for item in design.get('inherited_evidence',[]):check_entry(item)
    return design


def command(desired,text,run=RUN):
    assert desired in ['running','paused']
    with lock(run/'control.lock'):
        previous=load(run/'control.json')
        if previous['desired']==desired:return previous
        row=dict(desired=desired,revision=previous['revision']+1,received_utc=utc(),user_instruction=text)
        save(run/'commands'/f'{row["revision"]:06d}.json',row);save(run/'control.json',row)
        return row


def wanted_pause(run=RUN):return load(run/'control.json')['desired']=='paused'


def update_status(run=RUN,**values):
    values.update(utc=utc(),profile=PROFILE);save(run/'status.json',values)


def segment_paths(out,design):
    result={name:Path(item['path']) for name,item in design.get('inherited_segments',{}).get(Path(out).name,{}).items()}
    for path in (Path(out)/'segments').glob('seg_*'):
        assert path.name not in result,'Repeated segment identifier'
        result[path.name]=path
    return result


def segment_finish(seg,out,design):
    seg=Path(seg)
    if (seg/'finished.json').exists():return load(seg/'finished.json')
    inherited=design.get('inherited_segments',{}).get(Path(out).name,{}).get(seg.name)
    if inherited and inherited.get('termination_audit'):
        check_entry(inherited['termination_audit']);audit=load(inherited['termination_audit']['path'])
    else:audit=load(seg/'supervisor_termination.json')
    assert audit['kind']=='controller_observed_termination' and audit['worker_exited']
    assert audit['process']==load(seg/'environment.json')['process']
    for item in audit['evidence']:check_entry(item)
    assert dt.datetime.fromisoformat(audit['started_utc'])<=dt.datetime.fromisoformat(audit['ended_utc'])
    return audit


def row_provenance(row,out,design,run,ledger):
    seg=segment_paths(out,design)[row['segment_id']]
    env=load(seg/'environment.json');start=load(seg/'started.json');finish=segment_finish(seg,out,design)
    assert row['process']==env['process']==start['process']==finish['process']
    assert env['cpu_pool']==1 and env['io_pool']==4
    begin,end=map(dt.datetime.fromisoformat,[row['started_utc'],row['ended_utc']])
    assert dt.datetime.fromisoformat(start['started_utc'])<=begin<=end<=dt.datetime.fromisoformat(finish['ended_utc'])
    assert row['design_sha256']==start['design_sha256']
    if row['design_sha256']==sha(Path(run)/'design.json'):
        applicable=design
    else:
        assert row['design_sha256']==design['previous_design']['sha256']
        applicable=load(design['previous_design']['path'])
    assert row['time_policy']==applicable['time_policy']
    assert dt.datetime.fromisoformat(ledger['started_utc'])<=begin
    assert elapsed_active(ledger,end)<applicable['measurement_budget_seconds']
    for pause in ledger['pauses']:
        left=dt.datetime.fromisoformat(pause['paused_utc'])
        right=dt.datetime.fromisoformat(pause['resumed_utc']) if pause.get('resumed_utc') else dt.datetime.max.replace(tzinfo=dt.timezone.utc)
        assert end<=left or begin>=right
    return seg


def main_records(item):
    out=Path(item['path'])
    if item['origin']=='legacy':
        with (out/'timings.jsonl').open(encoding='utf-8') as f:return [json.loads(line) for line in f]
    return [r for commit in checkpoint(out)['commits'] for r in load(commit['path'])['records']]


def measurement_budget_check(design,ledger,now=None):
    assert elapsed_active(ledger,now)<design['measurement_budget_seconds'],'Extended measurement budget exhausted'

