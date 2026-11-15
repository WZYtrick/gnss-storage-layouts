"""Read-only verification of the local, not-yet-published reproduction package."""
from pathlib import Path
import json,hashlib,os,re
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main():
    package=ROOT/'release/candidate_v4';manifest=load(package/'release_manifest.json')
    expected={r['path'] for r in manifest}|{'release_manifest.json'}
    actual={p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
    assert actual==expected,dict(extra=sorted(actual-expected),missing=sorted(expected-actual))
    patterns={'workspace':re.compile(r'Obsidian_workspace',re.I),
              'user_path':re.compile(r'[A-Z]:[\\/]+Users[\\/]+16796',re.I),
              'email':re.compile(r'REDACTED_IDENTITY\.com',re.I),
              'author':re.compile(r'REDACTED_IDENTITY|REDACTED_IDENTITY|REDACTED_IDENTITY|REDACTED_IDENTITY|REDACTED_IDENTITY|REDACTED_IDENTITY',re.I)}
    hostname=os.environ.get('COMPUTERNAME')
    if hostname and len(hostname)>4:patterns['host_identifier']=re.compile(re.escape(hostname),re.I)
    leaked=[];total=0
    for item in manifest:
        p=package/item['path'];assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],item['path']
        total+=item['bytes']
        if p.suffix in ['.json','.jsonl','.csv','.md','.py','.txt']:
            text=p.read_text(encoding='utf-8')
            for name,pattern in patterns.items():
                if pattern.search(text):leaked.append(dict(path=item['path'],kind=name))
    assert not leaked,leaked
    cert=load(ROOT/'portability/construction_sample_v4/block_00/verification.json')
    assert cert['all_passed'] and cert['sample'] and len(cert['reports'])==34
    assert all(r['data_file_hashes_match'] and r['all_passed'] for r in cert['reports'])
    replay=load(ROOT/'portability/analysis_v3/independent_verification.json');assert replay['all_passed']
    for name,hash_value in replay['output_hashes'].items():
        assert sha(package/'analysis/existing_evidence'/name)==hash_value,name
    tests=load(ROOT/'portability/regression_v4.json');assert tests['all_passed'] and tests['tests']==6
    report=dict(all_passed=True,published=False,utc=datetime.now(timezone.utc).isoformat(),
                package='release/candidate_v4',manifest_files=len(manifest),bytes=total,
                manifest_sha256=sha(package/'release_manifest.json'),identity_findings=leaked,
                sample_layouts=34,sample_query_and_task_checks=sum(r['checked_queries'] for r in cert['reports']),
                sample_data_file_hashes_match=True,independent_checks=replay['checks'],
                fast_replay_tables_identical_to_verified_relocation=True,regression_tests=tests['tests'])
    (ROOT/'validation/release_candidate_v4.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__':main()
