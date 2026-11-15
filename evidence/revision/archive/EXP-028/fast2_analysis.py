"""Two query blocks, six construction blocks; explicit mixed-session evidence."""
from fast2_support import *
import argparse
import csv
import itertools
import math
import statistics as st
from collections import defaultdict

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


def excluded_groups(out,cp):
    accepted={str(Path(x['path']).resolve()) for x in cp['commits']};excluded=[]
    for folder in (Path(out)/'segments').glob('*/scheduled'):
        for path in folder.glob('g*.json'):
            if str(path.resolve()) not in accepted:
                excluded.append(dict(**entry(path),reason='no committed checkpoint'))
        for path in folder.glob('g*.partial.jsonl'):
            completed=path.with_name(path.name.replace('.partial.jsonl','.json'))
            if not completed.exists():
                excluded.append(dict(**entry(path),reason='incomplete request group; replayed from its first method'))
    return excluded


def collect(manifest_path,allow_fixture=False):
    manifest=load(manifest_path);run=Path(manifest_path).parent
    fixture=manifest.get('purpose')=='fixture'
    assert (fixture and allow_fixture) or manifest['purpose']=='formal'
    if not fixture:design=verify_frozen(run)
    else:design=None
    assert manifest['query_blocks']==[0,1] and manifest['construction_blocks']==list(range(6))
    logs=[];medians=[];build=[];formal=warm=extra=0;excluded=[]
    for item in manifest['baselines']:
        ds,block=item['dataset'],item['block'];out=Path(item['path']);cert=load(out/'verification.json')
        assert cert['all_passed'] and cert['formal_calls']==4536 and cert['warmup_calls']==1512
        logs.append(entry(out/'verification.json'))
        _,qs,truth,_=inputs(ds);qmap={q['id']:q for q in qs};plan=schedule(qs,block)
        if fixture:
            with (out/'timings.jsonl').open(encoding='utf-8') as stream:rows=[json.loads(x) for x in stream]
            logs.append(entry(out/'timings.jsonl'))
        elif item['origin']=='legacy':
            check_entry(cert['authorization']);check_entry(cert['session_wrapper'])
            execution=load(out/'session_execution.json');assert execution['all_passed']
            proof=load(out/'session_call_audit.json');assert proof['all_passed'];check_entry(proof['timings'])
            rows=main_records(item);logs.append(proof['timings'])
            auth=load(cert['authorization']['path'])
        else:
            check_entry(cert['design']);check_entry(cert['checkpoint']);assert cert['design']['sha256']==sha(run/'design.json')
            cp=validate_checkpoint(out,plan);assert cp['next_group']==864
            rows=main_records(item);logs.append(entry(out/'checkpoint.json'))
            excluded.extend(excluded_groups(out,cp))
            for path in (out/'segments').glob('*/recovery/g*.json'):
                group=load(path);assert group['kind']=='recovery' and len(group['records'])==7
                seg=path.parent.parent;env=load(seg/'environment.json');plan_recovery=load(seg/'recovery_plan.json')['groups']
                assert group['group']==plan_recovery[int(path.stem[1:])]
                assert [r['method'] for r in group['records']]==group['group']['methods']
                for row in group['records']:
                    assert row['record_kind']=='recovery' and row['warmup']
                    assert row['process']==env['process'] and row['rows']==truth[row['query']]['rows'] and row['sha256']==truth[row['query']]['sha256']
                    assert row['design_sha256']==sha(run/'design.json') and row['time_policy']==POLICY;extra+=1
                logs.append(entry(path))
            ledger=load(run/'budget.json')
        assert len(rows)==6048
        groups=defaultdict(list);last_end=None
        for order,row in enumerate(rows):
            expected=plan[order//7]
            assert (row['query'],row['method'],row['repeat'])==(expected['query'],expected['methods'][order%7],expected['repeat'])
            assert row['call_order']==order and row['dataset']==ds and row['block']==block
            assert row['family']==qmap[row['query']]['family'] and row['contract']=='ordered_dense' and row['cpu']==1 and row['io']==4
            assert row['rows']==truth[row['query']]['rows'] and row['sha256']==truth[row['query']]['sha256']
            begin,end=map(dt.datetime.fromisoformat,[row['started_utc'],row['ended_utc']]);assert begin<=end
            if last_end is not None:assert begin>=last_end
            last_end=end
            if not fixture:
                if item['origin']=='legacy':
                    assert dt.datetime.fromisoformat(auth['started_utc'])<=begin<=end<dt.datetime.fromisoformat(auth['deadline_utc'])
                    assert row['process']==cert['process'] and row['time_policy']==auth['time_policy']
                else:
                    seg=out/'segments'/row['segment_id'];env=load(seg/'environment.json');start=load(seg/'started.json');finish=load(seg/'finished.json')
                    assert env['process']==row['process'] and env['cpu_pool']==1 and env['io_pool']==4
                    assert dt.datetime.fromisoformat(start['started_utc'])<=begin<=end<=dt.datetime.fromisoformat(finish['ended_utc'])
                    assert row['time_policy']==POLICY and row['record_kind']=='scheduled' and row['design_sha256']==sha(run/'design.json')
                    assert elapsed_active(ledger,end)<design['measurement_budget_seconds']
                    for pause in ledger['pauses']:
                        left=dt.datetime.fromisoformat(pause['paused_utc']);right=dt.datetime.fromisoformat(pause['resumed_utc']) if pause.get('resumed_utc') else dt.datetime.max.replace(tzinfo=dt.timezone.utc)
                        assert end<=left or begin>=right
            assert row['seconds']>0
            for phase in PHASES:assert row.get(phase) is None or math.isfinite(row[phase]) and row[phase]>=0
            assert math.isclose(row['seconds'],row['scan_seconds']+row['normalize_seconds']+row.get('overhead_seconds',0),rel_tol=1e-8,abs_tol=1e-7)
            assert row['warmup']==(row['repeat']<0)
            if row['warmup']:warm+=1
            else:groups[row['query'],row['method']].append(row);formal+=1
        for (qid,method),records in groups.items():
            assert len(records)==3
            row=sorted(records,key=lambda x:(x['seconds'],x['repeat']))[1]
            medians.append({k:row[k] for k in ['dataset','block','query','family','method','repeat','rows','call_order','started_utc']}|
                           {p:row.get(p) for p in PHASES})
    for item in manifest['constructions']:
        out=Path(item['path']);cert=load(out/'verification.json')
        assert cert['all_passed'] and cert['dataset']==item['dataset'] and cert['block']==item['block']
        assert not (out/'failure.json').exists()
        if not fixture:
            check_entry(cert['authorization']);check_entry(cert['session_wrapper'])
            execution=load(out/'session_execution.json');assert execution['all_passed']
            auth=load(cert['authorization']['path'])
            assert dt.datetime.fromisoformat(auth['started_utc'])<=dt.datetime.fromisoformat(cert['started_utc'])<=dt.datetime.fromisoformat(execution['ended_utc'])<dt.datetime.fromisoformat(auth['deadline_utc'])
            for name,value in cert['code'].items():assert value==design['original_runtime_code'][name]
        build.extend(cert['results']);logs.append(entry(out/'verification.json'))
    assert (formal,warm,len(build),len(medians))==(18144,6048,204,6048)
    assert {(r['dataset'],r['block']) for r in medians}=={(ds,b) for ds in ['D1','D2'] for b in [0,1]}
    return manifest,medians,build,dict(formal=formal,warmup=warm,recovery_warmup=extra,inputs=logs,excluded_uncommitted_groups=excluded)


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


def analyze(manifest_path,output,allow_fixture=False):
    deny_network();manifest,medians,build,evidence=collect(manifest_path,allow_fixture)
    summarize(manifest,medians,build,output)
    save(Path(output)/'verification.json',dict(all_passed=True,purpose=manifest['purpose'],source_manifest=entry(manifest_path),
         formal_calls=evidence['formal'],warmup_calls=evidence['warmup'],recovery_warmup_calls=evidence['recovery_warmup'],
         median_calls=len(medians),layout_builds=len(build),inputs=evidence['inputs'],excluded_uncommitted_groups=evidence['excluded_uncommitted_groups'],
         output_hashes={p.name:sha(p) for p in Path(output).glob('*.csv')},independent_verification_required=True))
    print('FAST2 analyzed: 18144 formal calls, 6048 planned warmups, 204 builds; independent check pending.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--allow-fixture',action='store_true')
    a=p.parse_args();analyze(a.source_manifest,a.output,a.allow_fixture)
