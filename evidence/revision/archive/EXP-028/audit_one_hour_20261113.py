"""Final independent audit of complete workers across the one-hour controller versions."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,math

ROOT=Path(__file__).resolve().parent
RUN='revision_v1_20261112'
SESSIONS=['construction_one_hour_20261113_v2','construction_one_hour_20261113_v3']
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for data in iter(lambda:f.read(1048576),b''):h.update(data)
    return h.hexdigest()
def check_entry(item):
    p=Path(item['path']);assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],p

def main():
    campaign=ROOT/'campaigns'/RUN;design=load(campaign/'design.json')
    latest=ROOT/'sessions'/SESSIONS[-1]
    assert (latest/'finished.json').exists() and (latest/'summary.json').exists(),'Wait for the limited measurements to stop'
    assert not (latest/'failure.json').exists()
    assert load(latest/'summary.json')['state']=='stopped_after_limited_session'
    initial=load(ROOT/'sessions/construction_one_hour_20261113/authorization.json')
    origin=datetime.fromisoformat(initial['started_utc']);deadline=datetime.fromisoformat(initial['deadline_utc'])
    assert (deadline-origin).total_seconds()==3600
    auth={};controller_issues=[]
    for index,name in enumerate(SESSIONS):
        folder=ROOT/'sessions'/name;a=load(folder/'authorization.json')
        assert a['original_design_sha256']==sha(campaign/'design.json')
        assert a['deadline_utc']==initial['deadline_utc'] and a['original_hour_started_utc']==initial['started_utc']
        script=ROOT/f'limited_session_v{index+2}.py';assert sha(script)==a['wrapper_sha256']
        assert a['unchanged_runtime_code']==design['runtime_code']
        assert a['time_policy']=='explicit_user_one_hour_20261113'
        assert (folder/'finished.json').exists();auth[name]=a
        if (folder/'failure.json').exists():
            issue=load(folder/'failure.json');message=issue['traceback']
            assert name.endswith('_v2') and 'PermissionError' in message and 'status.json.tmp' in message and 'os.replace' in message
            controller_issues.append(dict(session=name,kind='status metadata replacement failed; worker certificates independently verified',
                                          failure_sha256=sha(folder/'failure.json')))
    for name,value in design['runtime_code'].items():assert sha(ROOT/name)==value,name
    jobs={j['id']:j for j in design['jobs']};counts={ds:0 for ds in ['D0m','D1','D2','D2m']}
    details=[];file_count=0;byte_count=0
    for out in sorted((campaign/'runs').iterdir()):
        certificate=out/'verification.json'
        if not certificate.exists():continue
        cert=load(certificate)
        if cert.get('execution_session') not in auth:continue
        assert not (out/'failure.json').exists(),out
        execution=load(out/'session_execution.json');session=cert['execution_session'];a=auth[session]
        assert cert['all_passed'] and execution['all_passed'] and cert['purpose']=='formal'
        assert cert['time_policy']==execution['time_policy']==a['time_policy']
        check_entry(cert['authorization']);check_entry(cert['session_wrapper']);check_entry(execution['authorization'])
        assert Path(cert['authorization']['path']).resolve()==(ROOT/'sessions'/session/'authorization.json').resolve()
        assert cert['session_wrapper']['sha256']==a['wrapper_sha256']
        assert cert['process']==execution['process'] and execution['session']==session
        assert jobs[out.name]['kind']=='construction' and execution['job']==jobs[out.name]
        start=datetime.fromisoformat(cert['started_utc']);end=datetime.fromisoformat(execution['ended_utc'])
        assert datetime.fromisoformat(a['started_utc'])<=start<=datetime.fromisoformat(cert['ended_utc'])<=end<deadline
        for name,value in cert['code'].items():assert value==design['runtime_code'][name]
        ds=cert['dataset'];sources=load(ROOT/'inputs'/ds/'sources.json')['sources'];source_map={s['id']:s for s in sources}
        expected=load(ROOT/'inputs'/ds/'layout_metrics.json');original_paths=load(ROOT/'inputs'/ds/'layouts.json')
        assert cert['sources']==len(sources) and cert['block']==jobs[out.name]['block']
        assert set(cert['methods'])==set(expected) and len(cert['results'])==len(expected)
        assert {row['method'] for row in cert['results']}==set(expected)
        for row in cert['results']:
            method=row['method'];old=expected[method]
            assert row['dataset']==ds and row['block']==cert['block']
            for field in ['rows','files','row_groups']:assert row[field]==old[field],(ds,method,field)
            assert row['verified_rows']==sum(s['records'] for s in sources)
            full=row['full_source_hashes'];assert len(full)==len(sources) and {r['source_id'] for r in full}==set(source_map)
            assert all(r['rows']==source_map[r['source_id']]['records'] for r in full)
            for field in ['decompress_seconds','parse_seconds','build_seconds','init_seconds']:
                assert math.isfinite(row[field]) and row[field]>=0
            assert math.isclose(row['from_rnx_seconds'],sum(row[f] for f in ['parse_seconds','build_seconds','init_seconds']),rel_tol=1e-14)
            assert math.isclose(row['from_crx_seconds'],row['decompress_seconds']+row['from_rnx_seconds'],rel_tol=1e-14)
            catalog=load(out/'layouts'/method/'catalog.json');oldcatalog=load(Path(original_paths[method])/'catalog.json')
            key=lambda r:(r['source_id'],r['sha256'],r['bytes'])
            assert sorted(map(key,catalog))==sorted(map(key,oldcatalog)),(ds,method)
            for item in catalog:
                p=out/'layouts'/method/item['file'];assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],p
                file_count+=1;byte_count+=item['bytes']
        counts[ds]+=len(cert['results'])
        details.append(dict(job=out.name,session=session,dataset=ds,block=cert['block'],layouts=len(cert['results']),
                            started_utc=cert['started_utc'],ended_utc=execution['ended_utc'],
                            elapsed_seconds=(end-start).total_seconds(),verification_sha256=sha(certificate)))
    details.sort(key=lambda r:r['started_utc'])
    assert all(details[i]['ended_utc']<=details[i+1]['started_utc'] for i in range(len(details)-1))
    actual_last=set(r['job'] for r in details if r['session']==SESSIONS[-1])
    assert actual_last=={r['job'] for r in load(latest/'summary.json')['completed']}
    assert (ROOT/'STOP_AFTER_BLOCK').exists()
    result=dict(all_passed=True,utc=datetime.now(timezone.utc).isoformat(),original_hour_started_utc=initial['started_utc'],
        deadline_utc=initial['deadline_utc'],measurement_stopped_utc=load(latest/'summary.json')['utc'],
        complete_blocks=len(details),layout_builds=sum(counts.values()),by_dataset=counts,
        data_files_rehashed=file_count,data_bytes_rehashed=byte_count,
        worker_active_seconds=sum(d['elapsed_seconds'] for d in details),details=details,controller_issues=controller_issues,
        excluded_attempt='retained_attempts/construction_one_hour_20261113/build_D0m_b00',
        original_runtime_code_unchanged=True,
        original_D2_predictions_unchanged=sha(ROOT/'evidence/freezes/D2_predictions.json')==design['old_D2_predictions_sha256'],
        remaining_formal_query_calls=54432,remaining_warmup_query_calls=18144,remaining_layout_builds=204-sum(counts.values()),
        remaining_measurements_deferred=True,analysis_requires_explicit_session_policy=True)
    assert result['original_D2_predictions_unchanged']
    target=ROOT/'sessions/one_hour_20261113_audit.json';target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    manifest=dict(original_run=RUN,original_design_sha256=sha(campaign/'design.json'),
        independent_audit_sha256=sha(target),sessions=[dict(session=name,
            authorization=dict(path=str(ROOT/'sessions'/name/'authorization.json'),sha256=sha(ROOT/'sessions'/name/'authorization.json')),
            completed_jobs=[d['job'] for d in details if d['session']==name]) for name in SESSIONS])
    (campaign/'authorized_session_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='details'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
