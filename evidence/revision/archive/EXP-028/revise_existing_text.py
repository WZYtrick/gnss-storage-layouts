"""Emit traceable, localized revisions supported by already verified evidence."""
from common import *
import re
import subprocess

PAPER=ROOT.parents[1]/'论文初稿/20261112-CMSDA2026返修稿'
ARS=Path('user_profile/.codex/skills/academic-research-suite/ars')

def main():
    base=PAPER/'working/manuscript.anchored.md'
    manifest=load(str(base)+'.block-manifest.json')
    text=base.read_text(encoding='utf-8')
    pieces=re.split(r'<!--block:(B\d+)-->\s*\n',text)
    blocks={pieces[i]:pieces[i+1].strip() for i in range(1,len(pieces),2)}
    hashes={x['block_id']:x['old_hash'] for x in manifest['blocks']}
    ops=[]
    def replace(prefix,new,ids):
        matches=[bid for bid,body in blocks.items() if body.startswith(prefix)]
        assert len(matches)==1,(prefix,matches)
        bid=matches[0]
        ops.append(dict(op='replace_block',block_id=bid,old_hash=hashes[bid],new_text=new,roadmap_item_ids=ids))
    def append(prefix,addition,ids):
        bid=next(b for b,v in blocks.items() if v.startswith(prefix))
        ops.append(dict(op='insert_after',block_id=bid,old_hash=hashes[bid],new_text=addition,roadmap_item_ids=ids))
    original=next(v for v in blocks.values() if v.startswith('The 120-minute interval'))
    replacement=('The 120-minute interval and 8192-row setting were selected on a separate six-file training set from January 2024 and frozen on October 27, 2026, before the April/July mechanism data were evaluated. '
                 'Training compared time-major and satellite-major orders with 1024, 8192, and 65536 rows for W/C, and 30-, 60-, and 120-minute blocks for T/F. '
                 'Each layout family was selected by geometric mean query time, with a 3% tolerance followed by storage size and configuration name. ')
    replace('The 120-minute interval',replacement+'A local schema'+original.split('A local schema',1)[1],['R3'])
    replace('Public daily files sampled',
       'Public daily files sampled every 30 seconds provide the observations.[2] Table 2 distinguishes parameter selection, mechanism development, rule development, and independent confirmation. The initial training set contains BRUX, POTS, and WTZR on January 20 and 28, 2024. D0m contains ABMF, ALGO, BRST, CEDU, KIRU, and MIZU on April 1 and July 1, 2024. D0m supports mechanism development and screening of additional layouts; D1 supports the full comparison and recommendation-rule development. D2 evaluates the frozen choices on different stations and dates. Source hashes confirm that the training set, D0m, D1, and D2 are disjoint.', ['R3'])
    replace('| Dataset | Dates and files | Records |',
       '| Dataset and role | Dates and files | Records |\n| --- | --- | --- |\n'
       '| Initial parameter training | Jan. 20 and 28, 2024; 6 files | 907,759 |\n'
       '| D0m: mechanism development | Apr. 1 and Jul. 1, 2024; 12 files | 1,658,423 |\n'
       '| D1: rule development | Oct. 1-6, 2024; 36 files | 4,472,284 |\n'
       '| D2: independent confirmation | Apr. 1-6, 2025; 36 files | 4,266,063 |',['R3'])
    replace('D1 uses ALIC,',
       'D1 uses ALIC, DRAO, GRAZ, HOB2, ONSA, and ZIMM, in that query-generation order. D2 uses FAIR, NYA2, REYK, DUBO, ALBH, and MAS1, in that order. Its mechanism subset D2m contains April 1 and 4: 12 files and 1,427,536 records. D2 stations follow the predetermined priority list, advancing only when a required source is unavailable. D0m estimates describe mechanism development; D2m provides the independent mechanism confirmation. Neither subset was used to select the initial 8192-row and 120-minute settings.',['R3','R5'])
    replace('Each full dataset has 216 queries,',
       'Each full dataset has 216 queries, with 36 in each family. For zero-based station index k, the usual start is 3k hours plus 330 seconds after midnight. Q1 selects one station, all codes, and 30 minutes. Q2 selects the fixed three-station group containing k, GPS codes C1C/L1C/S1C, and 15 minutes. Q3 selects one station, GPS satellite G03/G08/G13 cyclically by k, codes C1C/L1C/D1C/S1C, and two hours. Q4 selects the same three-station group, all S codes, and four hours starting at 3k hours. Q5 selects one station, all C/L codes, and the complete day. Q6 selects one station, BeiDou codes C2I/L2I/S2I, and three hours. Groups are stations 0-2 and 3-5. Times use source-calendar labels and half-open intervals. All-code projections use the dataset-wide code list. Each dataset retains 30 naturally empty queries; each mechanism subset uses the same rules for 72 queries. Exact requests and complete code lists accompany the supplementary material.',['R5'])
    append('Each experimental module has six paired blocks,',
       'A fresh worker process executes each configuration and block. Reader initialization preloads fragment metadata outside query timing. The Arrow scanner uses a batch size of 131072, batch readahead of 16, fragment readahead of 4, metadata caching, and pre-buffering. Garbage collection runs before each warm-up or measured pass. Request order uses seed 627100 plus the block index; per-request layout order is determined by the SHA-256 hash of seed, block, pass, request identifier, and layout name. The monotonic high-resolution performance counter measures each call; output verification and logging follow the timed return. Supplementary material records the complete configuration order and batch-specific timing policies.',['R5'])
    old=next(v for v in blocks.values() if v.startswith('| Numerator / denominator |'))
    replace('| Numerator / denominator |',old.replace('Dev. 1 | Dev. 8 | D2 1 | D2 8','D0m: 1 | D0m: 8 | D2m: 1 | D2m: 8'),['R3'])
    replace('Each cell covers 72 queries',
       'Each cell covers 72 queries on D0m or D2m, as labeled; the numbers after the colon are CPU compute-pool capacities. D0m is the April/July 2024 mechanism subset, not D1. Ratios below one favor the numerator.',['R3'])
    replace('C* also uses less storage than C:',
       'C* occupies 395.554 MiB on D1 and 356.329 MiB on D2, compared with 427.551 and 384.345 MiB for C: reductions of 7.48% and 7.29%. Each pair has identical data-file counts, 150 on D1 and 174 on D2. One MiB equals 2 to the power of 20 bytes; totals include required layout metadata. Supplementary Table S1 reports absolute sizes, metadata overhead, and file counts for all 34 measured dataset-layout combinations.',['R2'])
    old=next(v for v in blocks.values() if v.startswith('Ordinary-path stage timing'))
    replace('Ordinary-path stage timing',old.replace('The D1 measurements at capacities four and sixteen also show that query cost does not decrease monotonically with capacity.',
       'For C with ordered matrices, D1 cumulative times at capacities 1, 4, 8, and 16 are 9.565, 7.627, 7.756, and 7.898 s. Thus, capacity four gives the lowest cumulative time among these settings. Supplementary Figure S1 and Table S2 show all five layouts, three output forms, and four capacities, with all six block values.'),['R4'])
    append('Figure 2 shows that conditional selection',
       'Sensitivity analysis replays 1%, 3%, and 5% near-optimal tolerances using D1 costs and evaluates each on the same 3036 D2 scenarios. Mean excess costs are 0.762%, 1.393%, and 1.774%, and maxima are 15.425%, 16.439%, and 19.118%, respectively. Relative to the frozen 3% rule, 759 recommendations change at 1% and 613 at 5%. These post hoc comparisons describe tolerance sensitivity; the original 3% rule remains the primary result. Separately changing the additional-candidate speed and storage thresholds to 1%, 3%, 5%, or 10%, or its near-optimal tolerance to 1%, 3%, or 5%, admits none of the seven layouts. Other conditions, including improvement in at least five blocks, remain fixed.',['R4'])
    patch=dict(patch_format_version='1.0',revision_round=1,base_draft_hash=manifest['base_draft_hash'],ops=ops,emitted_by='revision_writer')
    patchpath=PAPER/'working/revision_patch_round1.json';dump(patchpath,patch)
    target=PAPER/'working/manuscript.revised_stage1.md'
    if target.exists():raise FileExistsError(target)
    document_python=os.environ.get('EXP028_DOCUMENT_PYTHON',sys.executable)
    subprocess.run([document_python,'-B',str(ARS/'scripts/ars_apply_revision_patch.py'),str(base),str(patchpath),'--output',str(target)],
                   env={**os.environ,'PYTHONPATH':str(PAPER/'tools/_deps')},check=True)
    cleaned=re.sub(r'<!--block:B\d+-->\s*\n','',target.read_text(encoding='utf-8'))
    (PAPER/'source/manuscript.md').write_text(cleaned,encoding='utf-8')
    dump(PAPER/'checks/revision_status.json',dict(status='working_revision_pending_new_experiments_and_public_release',
        items={'R1':'baseline smoke passed; full validation and formal measurement pending','R2':'absolute footprint complete; construction formal measurements pending',
               'R3':'corrected with disjoint training-source evidence','R4':'full thread and sensitivity evidence independently verified',
               'R5':'raw-call replay verified; portable public release pending'},
        original_manuscript_sha256=sha(PAPER/'working/manuscript.submitted.md'),stage1_patch=str(patchpath),
        existing_evidence_audit=entry(ROOT/'validation/independent_existing_evidence.json')))
    print('Applied',len(ops),'localized manuscript changes; new-result sections remain pending.')

if __name__=='__main__':main()
