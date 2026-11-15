"""Audit completed one-hour construction measurements and their explicit authorization."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,hashlib,math

ROOT=Path(__file__).resolve().parent
SESSION='construction_one_hour_20261113_v2'
RUN='revision_v1_20261112'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()
def valid_entry(e):
    p=Path(e['path']);assert p.stat().st_size==e['bytes'] and sha(p)==e['sha256'],p
def main():
    session=ROOT/'sessions'/SESSION;campaign=ROOT/'campaigns'/RUN
    assert (session/'finished.json').exists(),'Do not perform a heavy final audit during measurement'
    authorization=load(session/'authorization.json');summary=load(session/'summary.json')
    design=load(campaign/'design.json');assert sha(campaign/'design.json')==authorization['original_design_sha256']
    assert sha(ROOT/'limited_session_v2.py')==authorization['wrapper_sha256']
    for name,digest in design['runtime_code'].items():assert sha(ROOT/name)==digest,name
    begin=datetime.fromisoformat(authorization['started_utc']);end=datetime.fromisoformat(authorization['deadline_utc'])
    origin=datetime.fromisoformat(authorization['original_hour_started_utc'])
    assert (end-origin).total_seconds()==3600 and begin>=origin
    assert summary['state']=='stopped_after_limited_session' and not (session/'failure.json').exists()
    assert (ROOT/'STOP_AFTER_BLOCK').exists()
    counts={ds:0 for ds in ['D0m','D1','D2','D2m']};details=[];data_files=0;data_bytes=0
    for completed in summary['completed']:
        out=campaign/'runs'/completed['job'];assert not (out/'failure.json').exists()
        cert=load(out/'verification.json');execution=load(out/'session_execution.json')
        assert cert['all_passed'] and execution['all_passed']
        assert cert['execution_session']==SESSION and execution['session']==SESSION
        assert cert['time_policy']==execution['time_policy']==authorization['time_policy']
        valid_entry(cert['authorization']);valid_entry(cert['session_wrapper']);valid_entry(completed['verification'])
        assert Path(cert['authorization']['path']).resolve()==(session/'authorization.json').resolve()
        assert begin<=datetime.fromisoformat(cert['started_utc'])<=datetime.fromisoformat(cert['ended_utc'])<=datetime.fromisoformat(execution['ended_utc'])<end
        assert cert['process']==execution['process']
        for name,digest in cert['code'].items():assert digest==design['runtime_code'][name]
        dataset=cert['dataset'];sources=load(ROOT/'inputs'/dataset/'sources.json')['sources'];byid={s['id']:s for s in sources}
        expected=load(ROOT/'inputs'/dataset/'layout_metrics.json');original_paths=load(ROOT/'inputs'/dataset/'layouts.json')
        assert set(cert['methods'])==set(expected) and len(cert['results'])==len(expected)
        assert cert['sources']==len(sources) and cert['block']==completed['block']
        for result in cert['results']:
            method=result['method'];old=expected[method]
            for field in ['rows','files','row_groups']:assert result[field]==old[field],(dataset,method,field)
            assert result['verified_rows']==sum(s['records'] for s in sources)
            full=result['full_source_hashes'];assert {r['source_id'] for r in full}==set(byid)
            assert all(r['rows']==byid[r['source_id']]['records'] for r in full)
            for field in ['decompress_seconds','parse_seconds','build_seconds','init_seconds']:
                assert math.isfinite(result[field]) and result[field]>=0
            assert math.isclose(result['from_rnx_seconds'],sum(result[f] for f in ['parse_seconds','build_seconds','init_seconds']),rel_tol=1e-14)
            assert math.isclose(result['from_crx_seconds'],result['decompress_seconds']+result['from_rnx_seconds'],rel_tol=1e-14)
            current=load(out/'layouts'/method/'catalog.json');oldcat=load(Path(original_paths[method])/'catalog.json')
            fields=lambda row:(row['source_id'],row['sha256'],row['bytes'])
            assert sorted(map(fields,current))==sorted(map(fields,oldcat)),(dataset,method)
            for item in current:
                p=out/'layouts'/method/item['file'];assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],p
                data_files+=1;data_bytes+=item['bytes']
        counts[dataset]+=len(cert['results'])
        details.append(dict(job=completed['job'],dataset=dataset,block=cert['block'],layouts=len(cert['results']),
                            elapsed_seconds=completed['elapsed_seconds'],started_utc=cert['started_utc'],ended_utc=execution['ended_utc'],
                            verification_sha256=sha(out/'verification.json'),execution_sha256=sha(out/'session_execution.json')))
    assert sum(counts.values())==summary['layout_builds']
    result=dict(all_passed=True,utc=datetime.now(timezone.utc).isoformat(),session=SESSION,
        complete_blocks=len(details),layout_builds=sum(counts.values()),by_dataset=counts,
        data_files_rehashed=data_files,data_bytes_rehashed=data_bytes,
        one_hour_authorization_verified=True,measurement_code_unchanged=True,details=details,
        original_D2_predictions_unchanged=sha(ROOT/'evidence/freezes/D2_predictions.json')==design['old_D2_predictions_sha256'],
        excluded_attempt='retained_attempts/construction_one_hour_20261113/build_D0m_b00',
        remaining_formal_query_calls=54432,remaining_warmup_query_calls=18144,remaining_layout_builds=204-sum(counts.values()),
        analysis_requires_explicit_session_policy=True)
    assert result['original_D2_predictions_unchanged']
    (session/'independent_audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    manifest=dict(original_run=RUN,original_design_sha256=sha(campaign/'design.json'),
        sessions=[dict(session=SESSION,authorization=dict(path=str(session/'authorization.json'),sha256=sha(session/'authorization.json')),
                       completed_jobs=[d['job'] for d in details],audit_sha256=sha(session/'independent_audit.json'))])
    (campaign/'authorized_session_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='details'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
