"""Build a path-independent, identity-scrubbed local release candidate."""
from common import *
import argparse
import csv
import re
import shutil
import zipfile

CODE=['common.py','storage.py','reader.py','layouts.py','tasks.py','direct_reader.py','georinex_reader.py',
      'construction.py','campaign.py','validate_baseline.py','evidence_analysis.py','independent_review.py',
      'test_revision.py','test_revision_analysis.py','reproduce.py','plot_results.py','analyze_revision.py','independent_revision.py','render_revision.py',
      'requirements.txt','requirements.lock.txt','protocol.md']

def scrub(value):
    if isinstance(value,dict):
        return {scrub(k):scrub(v) for k,v in value.items() if str(k).lower() not in ['hostname','computer_name','username','user_name']}
    if isinstance(value,list):return [scrub(x) for x in value]
    if isinstance(value,str) and re.match(r'^[A-Za-z]:[\\/]',value):
        normalized=value.replace('\\','/')
        match=re.search(r'/EXP-(\d{3})/(.*)',normalized)
        if match:return 'historical/EXP-'+match.group(1)+'/'+match.group(2)
        return 'external/'+normalized.rsplit('/',1)[-1]
    return value

def main(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    provenance=[]
    def save(src,relative):
        dst=destination/relative;dst.parent.mkdir(parents=True,exist_ok=True)
        if src.suffix=='.json':dump(dst,scrub(load(src)))
        elif src.suffix=='.csv':
            with src.open(encoding='utf-8-sig',newline='') as f:
                rows=list(csv.reader(f))
            revised=scrub(rows)
            if revised==rows:shutil.copyfile(src,dst)
            else:
                with dst.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows(revised)
        else:shutil.copyfile(src,dst)
        provenance.append(dict(path=str(relative).replace('\\','/'),original_sha256=sha(src),published_sha256=sha(dst),bytes=dst.stat().st_size))
    for name in CODE:save(ROOT/name,Path(name))
    for directory in ['inputs','evidence']:
        for p in sorted((ROOT/directory).rglob('*')):
            if p.is_file():save(p,p.relative_to(ROOT))
    for p in sorted((ROOT/'analysis/existing_evidence').glob('*')):
        if p.suffix in ['.csv','.json']:save(p,p.relative_to(ROOT))
    for p in sorted((ROOT/'analysis/metadata_inventory').glob('*')):
        if p.suffix in ['.csv','.json']:save(p,p.relative_to(ROOT))
    for name in ['independent_existing_evidence.json','regression_tests.json','analysis_regression_tests.json']:
        save(ROOT/'validation'/name,Path('validation')/name)
    # Relabel paths for external users; no original machine path is required.
    for ds in ['D0m','D1','D2','D2m']:
        p=destination/'inputs'/ds/'sources.json';manifest=load(p)
        for src in manifest['sources']:
            for field in ['source_file','compressed_file']:
                src[field]=Path(src[field]).name
            src['prepared_dir']='prepared/'+ds+'/'+src['name']
        dump(p,manifest)
        path=destination/'inputs'/ds/'layouts.json'
        dump(path,{method:'rebuilt/'+ds+'/layouts/'+method for method in load(path)})
    # Scrubbed metadata has a new checksum; raw timings remain byte-identical.
    raw=load(destination/'evidence/raw_manifest.json')
    for item in raw:
        p=destination/item['path'];item['original_sha256']=item['sha256'];item['bytes']=p.stat().st_size;item['sha256']=sha(p)
    dump(destination/'evidence/raw_manifest.json',raw)
    for item in provenance:
        p=destination/item['path'];item['published_sha256']=sha(p);item['bytes']=p.stat().st_size
    dump(destination/'release_provenance.json',dict(status='local_release_candidate_no_public_link_yet',files=provenance,
        note='Absolute local paths and machine identifiers are removed. Numeric data and raw timing rows are unchanged. Footprints describe the measured original stores, not this sanitized metadata package.'))
    (destination/'README.md').write_text(README,encoding='utf-8')
    (destination/'LICENSE').write_text(LICENSE,encoding='utf-8')
    (destination/'DATA_LICENSE.md').write_text('Original timing and derived result tables are provided under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Original GNSS files are not redistributed; their source URLs, checksums, and provenance are supplied. Third-party software retains its original license.\n',encoding='utf-8')
    allfiles=[p for p in destination.rglob('*') if p.is_file()]
    dump(destination/'release_manifest.json',[dict(path=str(p.relative_to(destination)).replace('\\','/'),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(allfiles)])
    print('Prepared portable release candidate:',len(allfiles),'files',sum(p.stat().st_size for p in allfiles),'bytes')

README='''# GNSS storage layout reproduction materials

This package reproduces the layout, output-contract, task, and frozen-rule analyses for **Storage Layouts and Query Costs for Global Navigation Satellite System Observations**. The current candidate contains the completed original study and the implementation/protocol of the reviewer-requested GeoRinex and ingestion experiments. New formal measurements are not represented as completed results until their logs and audits are added.

## Quick reproduction without the original GNSS files

Use Python 3.12.14. Install the pinned dependencies in a fresh virtual environment. From this directory run:

```text
python -m pip install -r requirements.lock.txt
python reproduce.py analyze --output reproduced_analysis
python independent_review.py --analysis-root reproduced_analysis
python plot_results.py --analysis-root reproduced_analysis --output reproduced_figures
python test_revision.py
```

The first command replays all 4,260 frozen predictions from per-request median calls. The independent checker reselects median calls from raw logs, checks stage timing and batch provenance, and recomputes the rule through a separate implementation. The 3,036 primary scenarios are 253 workload weights by two capacities, three contracts, and two metrics. The 1,224 historical alpha scenarios are separate. Relative cost values and selected layouts should match; CSV row order is not a scientific result.

## Rebuild directly from public source files

```text
python reproduce.py download --output raw_sources --datasets D0m D1 D2
python reproduce.py rebuild --source-root raw_sources --output rebuilt --datasets D0m D1 D2 D2m
```

If files are already available, skip downloading and provide their directory; filenames must be unique. Input hashes are checked before parsing. The output must be a new directory. Compressed-only input is supported. The rebuild checks prepared arrays, all three query contracts and the applicable complete task results. `--sample` rebuilds one source per dataset for a relocation smoke test; it is explicitly a subset check.

To run a fresh query benchmark after rebuilding:

```text
python reproduce.py benchmark --data-root rebuilt --output fresh_measurement --dataset D2 --methods W C Cstar T F GeoRinex_R GeoRinex_H --contract ordered_dense --cpu 1 --blocks 6 --repeats 3
```

This explicit benchmark command measures at the time it is invoked and labels its outputs as an external reproduction. It does not join the original measurements. Elapsed times depend on the host; returned-content hashes should agree. `campaign.py` is the separate original-host supervisor and enforces Beijing 01:00-08:00 and its full validation gates.

To reproduce fresh construction costs with six independently rebuilt copies:

```text
python reproduce.py construction --source-root raw_sources --output fresh_build_measurement --datasets D0m D1 D2 D2m --blocks 6
```

This path forces fresh decompression even if an expanded source already exists. Each output block records decompression, shared parsing, layout encoding/metadata creation, and initialization, with output checks outside those timers. `--sample --blocks 1` is available for a functional check and is labeled as a subset, not a complete measurement.

## Evidence map

- `inputs/`: all source metadata, exact queries, tasks, independent truth hashes, layout metrics.
- `evidence/raw/`: 276 configurations, their raw calls and execution metadata; E4 calibration calls are retained separately from ordinary three-call medians.
- `evidence/freezes/`: original development and D2 prediction records. Initial parameter training is in `evidence/training/`.
- `analysis/existing_evidence/`: complete D1 thread matrix, absolute storage, candidate thresholds, and replayed recommendation tables.
- `protocol.md`: revision protocol and timing boundaries.
- `release_provenance.json`: original and published checksums; local paths and machine identifiers are scrubbed.

GeoRinex 1.16.2 parses all observation numbers without modification. A timed adapter retains physical record identity and LLI/SSI flags. Calls use the native per-constellation path with time/code selection; compressed input is expanded once per request. No parsed-result cache is used. Unsupported duplicate epoch/satellite identities are rejected, not silently merged. Source-calendar labels are not converted to UTC.

The original data and scripts remain unchanged in the authors' workspace. Sanitized metadata bytes differ from the measured stores; published storage tables report the original frozen footprint. No DOI or public URL is claimed by this local release candidate.
'''

LICENSE='''MIT License

Copyright (c) 2026 The authors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
'''

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);main(p.parse_args().output)
