"""Portable revision tables; numerical summarizer preserved from measured study."""
from pathlib import Path
import csv,json,math,itertools,statistics as st
from collections import defaultdict
from revision_support import collect,load,inputs,entry,sha,dump,offline,ROOT,METHODS,CORE
FAMILIES=['Q'+str(i) for i in range(1,7)]
PHASES=['seconds','scan_seconds','normalize_seconds','decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']
BUILD_PHASES=['decompress_seconds','parse_seconds','build_seconds','init_seconds','from_rnx_seconds','from_crx_seconds']
def csvout(path,rows):
    assert rows
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,separators=(',',':')) if isinstance(v,(list,dict)) else v for k,v in row.items()})

def geo(values):return math.exp(st.fmean(math.log(v) for v in values))

def break_even(prep,direct,layout):return math.ceil(prep/(direct-layout)) if direct>layout else None

def weights():
    rows=[('simplex_'+''.join(map(str,x)),[v/5 for v in x]) for x in itertools.product(range(6),repeat=6) if sum(x)==5]
    rows.append(('equal',[1/6]*6));assert len(rows)==253
    return rows

def summarize(manifest,medians,build,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    csvout(output/'request_medians.csv',medians)
    grouped=defaultdict(list)
    for row in medians:
        for scope in ['all',row['family']]:grouped[row['dataset'],row['block'],row['method'],scope].append(row)
    blocks=[];lookup={}
    for key,records in sorted(grouped.items()):
        ds,block,method,scope=key;times=[r['seconds'] for r in records]
        assert len(times)==(216 if scope=='all' else 36)
        row=dict(dataset=ds,block=block,method=method,scope=scope,queries=len(records),A_ms=st.fmean(times)*1000,G_ms=geo(times)*1000,B_s=sum(times))
        for phase in PHASES[1:]:row[phase+'_sum']=sum(r[phase] for r in records) if records[0][phase] is not None else None
        blocks.append(row);lookup[key]=row
    csvout(output/'block_costs.csv',blocks)
    summaries=[]
    for ds in ['D1','D2']:
        for method in METHODS:
            for scope in ['all']+FAMILIES:
                rows=[lookup[ds,b,method,scope] for b in [0,1]];record=dict(dataset=ds,method=method,scope=scope,blocks=2)
                for metric in ['A_ms','G_ms','B_s']:
                    xs=[r[metric] for r in rows]
                    record.update({metric+'_geo':geo(xs),metric+'_arithmetic':st.fmean(xs),metric+'_min':min(xs),metric+'_max':max(xs),metric+'_blocks':xs})
                for phase in PHASES[1:]:
                    xs=[r[phase+'_sum'] for r in rows];record[phase+'_sum_blocks']=xs;record[phase+'_sum_mean']=st.fmean(xs) if xs[0] is not None else None
                summaries.append(record)
    csvout(output/'query_summary.csv',summaries)
    paired=[]
    for ds in ['D1','D2']:
        for scope in ['all']+FAMILIES:
            for method in CORE:
                for baseline in METHODS[-2:]:
                    for metric in ['A_ms','G_ms','B_s']:
                        ratios=[lookup[ds,b,method,scope][metric]/lookup[ds,b,baseline,scope][metric] for b in [0,1]]
                        paired.append(dict(dataset=ds,scope=scope,layout=method,baseline=baseline,metric=metric,blocks=2,
                                           ratio=geo(ratios),ratios=ratios,minimum=min(ratios),maximum=max(ratios),faster_blocks=sum(x<1 for x in ratios)))
    csvout(output/'paired_ratios.csv',paired)
    csvout(output/'build_blocks.csv',[{k:v for k,v in r.items() if not isinstance(v,(list,dict))} for r in build])
    buildgroups=defaultdict(list)
    for row in build:buildgroups[row['dataset'],row['method']].append(row)
    buildsummary=[];buildmap={}
    for (ds,method),rows in sorted(buildgroups.items()):
        rows.sort(key=lambda r:r['block']);assert [r['block'] for r in rows]==list(range(6))
        result=dict(dataset=ds,method=method,blocks=6)
        for phase in BUILD_PHASES:
            xs=[r[phase] for r in rows];result.update({phase+'_mean':st.fmean(xs),phase+'_min':min(xs),phase+'_max':max(xs),phase+'_blocks':xs})
        buildsummary.append(result);buildmap[ds,method]=rows
    assert len(buildsummary)==34
    csvout(output/'build_summary.csv',buildsummary)
    amortblocks=[];amort=[]
    for ds in ['D1','D2']:
        for method in CORE:
            for baseline in METHODS[-2:]:
                phase='from_rnx_seconds' if baseline.endswith('_R') else 'from_crx_seconds'
                preparations=[r[phase] for r in buildmap[ds,method]];meanprep=st.fmean(preparations)
                for scenario,w in weights():
                    direct=[sum(w[i]*lookup[ds,b,baseline,f]['A_ms']/1000 for i,f in enumerate(FAMILIES)) for b in [0,1]]
                    layout=[sum(w[i]*lookup[ds,b,method,f]['A_ms']/1000 for i,f in enumerate(FAMILIES)) for b in [0,1]]
                    for b in [0,1]:
                        amortblocks.append(dict(dataset=ds,method=method,baseline=baseline,scenario=scenario,weights=w,query_block=b,
                           mean_preparation_seconds=meanprep,direct_seconds_per_query=direct[b],layout_seconds_per_query=layout[b],
                           saving_seconds_per_query=direct[b]-layout[b],break_even_queries=break_even(meanprep,direct[b],layout[b])))
                    combinations=[break_even(prep,direct[b],layout[b]) for prep in preparations for b in [0,1]]
                    finite=[x for x in combinations if x is not None]
                    amort.append(dict(dataset=ds,method=method,baseline=baseline,scenario=scenario,weights=w,query_blocks=2,build_blocks=6,
                         mean_preparation_seconds=meanprep,mean_direct_seconds_per_query=st.fmean(direct),mean_layout_seconds_per_query=st.fmean(layout),
                         break_even_queries=break_even(meanprep,st.fmean(direct),st.fmean(layout)),finite_combinations=len(finite),
                         combination_min=min(finite) if finite else None,combination_max=max(finite) if finite else None,combination_values=combinations))
    assert len(amort)==5060 and len(amortblocks)==10120
    csvout(output/'amortization_query_blocks.csv',amortblocks);csvout(output/'amortization_summary.csv',amort)
    with (ROOT/'analysis/metadata_inventory/metadata_breakdown.csv').open(encoding='utf-8',newline='') as f:inventory=list(csv.DictReader(f))
    storage=[]
    for row in inventory:
        metric=load(ROOT/'inputs'/row['dataset']/'layout_metrics.json')[row['method']]
        storage.append(dict(dataset=row['dataset'],method=row['method'],parquet_bytes=int(row['parquet_bytes']),
            required_metadata_bytes=int(row['reader_metadata_bytes']),audit_metadata_bytes=int(row['audit_metadata_bytes']),
            total_MiB=int(row['retained_total_bytes'])/1024**2,data_files=metric['files'],required_metadata_files=int(row['reader_metadata_files']),
            audit_metadata_files=int(row['audit_metadata_files']),row_groups=metric['row_groups']))
    csvout(output/'storage_footprint.csv',storage)
    sources=[]
    for ds in ['D0m','D1','D2','D2m']:
        source=inputs(ds)[0]['sources'];sources.append(dict(dataset=ds,files=len(source),
             compressed_bytes=sum(s['compressed_bytes'] for s in source),expanded_bytes=sum(s['source_bytes'] for s in source)))
    csvout(output/'source_sizes.csv',sources)

def analyze(manifest_path,output):
    offline();output=Path(output)
    if output.exists():raise FileExistsError('Use a new output directory')
    manifest,medians,builds,audit=collect(manifest_path)
    summarize(manifest,medians,builds,output)
    dump(output/'verification.json',dict(all_passed=True,purpose='formal',source_manifest=entry(manifest_path),
         formal_calls=18144,warmup_calls=6048,recovery_warmup_calls=audit['recovery_calls'],median_calls=len(medians),
         layout_builds=len(builds),inputs=audit['inputs'],excluded_uncommitted_groups=manifest['excluded'],
         output_hashes={p.name:sha(p) for p in output.glob('*.csv')},independent_verification_required=True))
    print('Revision replay complete: 18144 formal calls, 204 builds; run verify-revision next.')
