"""Assemble local review archives; does not publish or contact an account."""
from pathlib import Path
import hashlib,json,shutil,zipfile
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()
def main():
    cert=load(ROOT/'validation/release_candidate_v4.json');assert cert['all_passed']
    source=ROOT/'release/candidate_v4'
    assert sha(source/'release_manifest.json')==cert['manifest_sha256']
    archive=ROOT/'release/GNSS_reproduction_review_candidate_v4.zip'
    files=sorted(p for p in source.rglob('*') if p.is_file())
    if not archive.exists():
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            for p in files:z.write(p,Path('GNSS_reproduction_review_candidate')/p.relative_to(source))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.infolist())==len(files)
        for p in files:
            data=z.read((Path('GNSS_reproduction_review_candidate')/p.relative_to(source)).as_posix())
            assert hashlib.sha256(data).hexdigest()==sha(p),p
    repo=ROOT/'release/github_candidate_v4';repo.mkdir(exist_ok=False)
    for p in files:
        relative=p.relative_to(source)
        if relative.parts[:2]==('evidence','raw') or relative.name in ['release_manifest.json','release_provenance.json']:continue
        dest=repo/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    with (repo/'README.md').open('a',encoding='utf-8') as f:
        f.write('\n## This code-and-processed-data staging copy\n\n'
                'This unpublished staging directory excludes the large `evidence/raw/` logs. '
                'The quick `reproduce.py analyze` path uses the included processed data and is complete. '
                'The independent raw-log checker additionally requires `evidence/raw/` from the full review archive. '
                'The matching local archive is `GNSS_reproduction_review_candidate_v4.zip`; '
                'public archive URLs will be inserted only after the final release succeeds.\n')
    (repo/'.gitignore').write_text('.venv/\n__pycache__/\n*.pyc\nreproduced_*/\nraw_sources/\nrebuilt/\nfresh_measurement/\nfresh_build_measurement/\n',encoding='utf-8')
    localfiles=sorted(p for p in repo.rglob('*') if p.is_file())
    for p in localfiles:assert p.stat().st_size<100*1024*1024,p
    manifest=[dict(path=p.relative_to(repo).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in localfiles]
    (repo/'release_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    report=dict(published=False,all_passed=True,utc=datetime.now(timezone.utc).isoformat(),
                full_archive=dict(path=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,sha256=sha(archive),
                                  files=len(files),all_members_hash_verified=True),
                github_staging=dict(path=str(repo.relative_to(ROOT)),files=len(localfiles),
                                    bytes=sum(p.stat().st_size for p in localfiles)))
    (ROOT/'validation/archive_packaging.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__':main()
