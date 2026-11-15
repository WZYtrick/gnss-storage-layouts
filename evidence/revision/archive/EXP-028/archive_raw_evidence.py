"""Preserve exact raw calls and map them to portable relative locations."""
from common import *
import shutil

def main():
    old=ROOT.parent/'EXP-027';entries=[];index=[]
    for label,name in [('D1','development_coverage_20261108.json'),('D2','D2_complete_coverage_20261110.json')]:
        coverage=load(old/'validation'/name);assert coverage['all_passed'] and not coverage['missing_configurations']
        hashes={x['path']:x for x in coverage['source_files']}
        for row in coverage['configuration_sources']:
            source=Path(row['source_directory'])
            dest=ROOT/'evidence/raw'/label/(f"b{row['block']:02d}_"+row['cell'])
            dest.mkdir(parents=True,exist_ok=True)
            copied={}
            for filename in ['timings.jsonl','environment.json','verification.json','design.json']:
                src=source/filename
                original=hashes[str(src)]
                assert src.stat().st_size==original['bytes'] and sha(src)==original['sha256']
                target=dest/filename
                if target.exists():assert sha(target)==original['sha256']
                else:shutil.copyfile(src,target)
                copied[filename]=str(target.relative_to(ROOT))
                entries.append(dict(path=str(target.relative_to(ROOT)),bytes=target.stat().st_size,sha256=sha(target)))
            index.append(dict(evidence=label,module=row['module'],cell=row['cell'],block=row['block'],
                              source_run_id=row['source_run_id'],time_policy=row['time_policy'],
                              recorded_session=row['recorded_session'],files=copied))
    dump(ROOT/'evidence/raw_runs.json',index);dump(ROOT/'evidence/raw_manifest.json',entries)
    print('Archived',len(index),'configurations;',sum(x['bytes'] for x in entries),'bytes')

if __name__=='__main__':main()
