"""Keep the conference narrative compact while exposing the reviewed thread claim."""
from common import *
import re
import subprocess

PAPER=ROOT.parents[1]/'论文初稿/20261112-CMSDA2026返修稿'
ARS=Path('user_profile/.codex/skills/academic-research-suite/ars')

def main():
    base=PAPER/'working/manuscript.revised_stage1.md';manifest=load(str(base)+'.block-manifest.json')
    chunks=re.split(r'<!--block:(B\d+)-->\s*\n',base.read_text(encoding='utf-8'))
    blocks={chunks[i]:chunks[i+1].strip() for i in range(1,len(chunks),2)}
    hashes={x['block_id']:x['old_hash'] for x in manifest['blocks']};ops=[]
    def add(kind,prefix,new=None,ids=['R4']):
        ids2=[b for b,v in blocks.items() if v.startswith(prefix)];assert len(ids2)==1
        bid=ids2[0];op=dict(op=kind,block_id=bid,old_hash=hashes[bid],roadmap_item_ids=ids)
        if new is not None:op['new_text']=new
        ops.append(op)
    add('replace_block','Each full dataset has 216 queries,',
        'Each full dataset has 216 queries, with 36 per family: Q1 selects 30 minutes and all codes at one station; Q2 selects 15 minutes of Global Positioning System (GPS) observations at three stations; Q3 selects two hours for one GPS satellite; Q4 selects four hours of signal strength at three stations; Q5 selects one day of pseudorange and phase at one station; and Q6 selects three hours of BeiDou observations at one station. Each dataset retains 30 naturally empty results. Mechanism subsets use 72 queries. Supplementary Table S5 and machine-readable requests specify station order, fixed groups, start times, half-open intervals, satellites, and complete code lists.',ids=['R5'])
    old=next(v for v in blocks.values() if v.startswith('Ordinary-path stage timing'))
    add('replace_block','Ordinary-path stage timing',old.replace('Thus, capacity four gives the lowest cumulative time among these settings.','Figure 2 shows the nonmonotonic response; capacity four gives the lowest cumulative time for C among these settings.'))
    add('insert_after','Output representation produces differences',
        '![Figure 2. D1 ordered-matrix cumulative query time B for 216 requests at four compute-pool capacities. Points are six-block geometric centers; error bars are block minima and maxima.](../figures/D1_threads_main.png)')
    add('delete_block','![Figure 2. Fixed-layout')
    old=next(v for v in blocks.values() if v.startswith('Figure 2 shows that conditional'))
    add('replace_block','Figure 2 shows that conditional',old.replace('Figure 2 shows','Supplementary Figure S3 shows'))
    patch=dict(patch_format_version='1.0',revision_round=2,base_draft_hash=manifest['base_draft_hash'],ops=ops,emitted_by='revision_writer')
    path=PAPER/'working/revision_patch_round2.json';dump(path,patch)
    target=PAPER/'working/manuscript.revised_stage2.md'
    subprocess.run([os.environ['EXP028_DOCUMENT_PYTHON'],'-B',str(ARS/'scripts/ars_apply_revision_patch.py'),str(base),str(path),'--output',str(target)],
          env={**os.environ,'PYTHONPATH':str(PAPER/'tools/_deps')},check=True)
    clean=re.sub(r'<!--block:B\d+-->\s*\n','',target.read_text(encoding='utf-8'))
    (PAPER/'source/manuscript.md').write_text(clean,encoding='utf-8')

if __name__=='__main__':main()
