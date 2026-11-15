"""Snapshot existing evidence without modifying any earlier experiment."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent

def digest(p):
    return hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()

def copy(src, dst, log):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if digest(src) != digest(dst):
            raise RuntimeError('Existing snapshot differs: ' + str(dst))
    else:
        shutil.copyfile(src, dst)
    log.append(dict(source=str(src.resolve()), destination=str(dst.relative_to(ROOT)), sha256=digest(dst)))

def main():
    log = []
    for name, experiment in [('storage.py','EXP-024'), ('direct_reader.py','EXP-024'),
                              ('reader.py','EXP-027'), ('layouts.py','EXP-027'), ('tasks.py','EXP-027')]:
        copy(BASE/experiment/name, ROOT/name, log)
    old = BASE/'EXP-027'
    for ds in ['D0m','D1','D2','D2m']:
        for name in ['sources','queries','truth','layouts','layout_metrics']:
            copy(old/'inputs'/ds/(name+'.json'), ROOT/'inputs'/ds/(name+'.json'), log)
        for name in ['tasks','task_truth']:
            p = old/'inputs'/ds/(name+'.json')
            if p.exists(): copy(p, ROOT/'inputs'/ds/(name+'.json'), log)
    for group, names in {
        'synthesis_20261110': ['costs.csv','layout_storage.csv','frozen_rule_weights.csv','frozen_threshold_scenarios.csv',
                             'frozen_threshold_summary.csv','mechanism_contrasts.csv','policy_context_comparison.csv',
                             'phases.csv','task_layout_effects.csv','synthesis_manifest.json'],
        'development_combined_20261109': ['block_metrics.csv','request_medians.csv','qualification.csv',
                                         'independent_verification.json','source_descriptor.json'],
        'd2_combined_20261110': ['block_metrics.csv','request_medians.csv','independent_verification.json','source_descriptor.json']
    }.items():
        for name in names:
            copy(old/'analysis'/group/name, ROOT/'evidence'/group/name, log)
    for name in ['D2_predictions.json','development_v1.json','d2_confirmation_20261109.json']:
        copy(old/'frozen'/name, ROOT/'evidence'/'freezes'/name, log)
    copy(BASE/'EXP-023/runs/train/selection.json', ROOT/'evidence/training/selection.json', log)
    copy(BASE/'EXP-023/protocol.md', ROOT/'evidence/training/protocol.md', log)
    for station in ['BRUX','POTS','WTZR']:
        for day in ['020','028']:
            copy(BASE/'EXP-023/prepared'/f'{station}_{day}'/'source.json',
                 ROOT/'evidence/training'/f'{station}_{day}.json', log)
    (ROOT/'snapshot_manifest.json').write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Snapshot:',len(log),'files')

if __name__ == '__main__': main()
