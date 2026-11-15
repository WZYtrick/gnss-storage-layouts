"""Independent FAST2 recomputation: stdlib only, no primary analyzer imports."""
import argparse
import csv
import hashlib
import itertools
import json
import math
import random
import socket
from pathlib import Path
from collections import defaultdict
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent
METHODS=['W','C','Cstar','T','F','GeoRinex_R','GeoRinex_H']
PHASES=['seconds','scan_seconds','normalize_seconds','decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']
BUILD_PHASES=['decompress_seconds','parse_seconds','build_seconds','init_seconds','from_rnx_seconds','from_crx_seconds']
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def table(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def check_entry(item):
    p=Path(item['path']);assert p.stat().st_size==item['bytes'] and digest(p)==item['sha256'],p
def close(a,b):return math.isclose(float(a),float(b),rel_tol=2e-12,abs_tol=1e-10)
def mean(xs):return math.fsum(xs)/len(xs)
def geo(xs):return math.exp(mean([math.log(x) for x in xs]))
def equal_nullable(cell,value):return cell=='' if value is None else close(cell,value)
def n_queries(prep,direct,layout):return None if direct<=layout else math.ceil(prep/(direct-layout))


def segment_locations(out,design):
    found={name:Path(item['path']) for name,item in design.get('inherited_segments',{}).get(Path(out).name,{}).items()}
    for seg in (Path(out)/'segments').glob('seg_*'):
        assert seg.name not in found;found[seg.name]=seg
    return found


def validate_origin(row,out,design,run,ledger):
    seg=segment_locations(out,design)[row['segment_id']]
    env=read(seg/'environment.json');start=read(seg/'started.json')
    if (seg/'finished.json').exists():finish=read(seg/'finished.json')
    else:
        previous=design.get('inherited_segments',{}).get(Path(out).name,{}).get(seg.name,{})
        if previous.get('termination_audit'):
            check_entry(previous['termination_audit']);finish=read(previous['termination_audit']['path'])
        else:finish=read(seg/'supervisor_termination.json')
        assert finish['kind']=='controller_observed_termination' and finish['worker_exited']
        for evidence in finish['evidence']:check_entry(evidence)
    assert row['process']==env['process']==start['process']==finish['process']
    assert env['cpu_pool']==1 and env['io_pool']==4
    begin,end=map(datetime.fromisoformat,[row['started_utc'],row['ended_utc']])
    assert datetime.fromisoformat(start['started_utc'])<=begin<=end<=datetime.fromisoformat(finish['ended_utc'])
    assert row['design_sha256']==start['design_sha256']
    if row['design_sha256']==digest(Path(run)/'design.json'):applicable=design
    else:
        assert row['design_sha256']==design['previous_design']['sha256'];applicable=read(design['previous_design']['path'])
    assert row['time_policy']==applicable['time_policy']
    paused=0.
    for pause in ledger['pauses']:
        left=datetime.fromisoformat(pause['paused_utc']);right=datetime.fromisoformat(pause['resumed_utc']) if pause.get('resumed_utc') else datetime.max.replace(tzinfo=timezone.utc)
        assert end<=left or begin>=right;paused+=max(0.,(min(end,right)-left).total_seconds())
    assert datetime.fromisoformat(ledger['started_utc'])<=begin
    assert (end-datetime.fromisoformat(ledger['started_utc'])).total_seconds()-paused<applicable['measurement_budget_seconds']


def verify(manifest_path,output,allow_fixture=False):
    def offline(*args,**kwargs):raise RuntimeError('FAST2 verification is offline')
    socket.create_connection=offline;socket.socket.connect=offline
    manifest_path=Path(manifest_path);output=Path(output);manifest=read(manifest_path);proof=read(output/'verification.json')
    fixture=manifest['purpose']=='fixture';assert not fixture or allow_fixture
    assert proof['all_passed'] and proof['purpose']==manifest['purpose']
    check_entry(proof['source_manifest']);assert proof['source_manifest']['sha256']==digest(manifest_path)
    for name,value in proof['output_hashes'].items():assert digest(output/name)==value,name
    for item in proof['inputs']:check_entry(item)
    if not fixture:
        design=read(manifest_path.parent/'design.json');check_entry(design['old_design'])
        check_entry(design['previous_design'])
        for name,value in read(design['previous_design']['path'])['fast_code'].items():assert digest(ROOT/name)==value
        for evidence in design['inherited_evidence']:check_entry(evidence)
        for name,value in design['fast_code'].items():assert digest(ROOT/name)==value
        for name,value in design['original_runtime_code'].items():assert digest(ROOT/name)==value
    medians={};builds={};rawcalls=0;checks=0;extra=0;excluded=set()
    for item in manifest['baselines']:
        out=Path(item['path']);cert=read(out/'verification.json');assert cert['all_passed']
        if fixture or item['origin']=='legacy':
            with (out/'timings.jsonl').open(encoding='utf-8') as f:records=[json.loads(line) for line in f]
        else:
            cp=read(out/'checkpoint.json');assert cp['next_group']==864 and len(cp['commits'])==864
            check_entry(cert['checkpoint']);records=[]
            accepted={str(Path(x['path']).resolve()) for x in cp['commits']}
            locations=segment_locations(out,design)
            for segment in locations.values():
                folder=segment/'scheduled'
                for p in folder.glob('g*.json'):
                    if str(p.resolve()) not in accepted:excluded.add(str(p.resolve()))
                for p in folder.glob('g*.partial.jsonl'):
                    if not p.with_name(p.name.replace('.partial.jsonl','.json')).exists():excluded.add(str(p.resolve()))
                for p in (segment/'recovery').glob('g*.partial.jsonl'):
                    if not p.with_name(p.name.replace('.partial.jsonl','.json')).exists():excluded.add(str(p.resolve()))
            for index,commit in enumerate(cp['commits']):
                check_entry(commit);batch=read(commit['path']);assert batch['kind']=='scheduled' and batch['group']['index']==index
                assert len(batch['records'])==7;records.extend(batch['records'])
            for p in [p for segment in locations.values() for p in (segment/'recovery').glob('g*.json')]:
                batch=read(p);assert batch['kind']=='recovery' and len(batch['records'])==7
                seg=p.parent.parent;plan=read(seg/'recovery_plan.json')['groups'];env=read(seg/'environment.json')
                expected_group=plan[int(p.stem[1:])];assert batch['group']==expected_group
                truth=read(ROOT/'inputs'/item['dataset']/'truth.json')
                ledger=read(manifest_path.parent/'budget.json')
                for r,method in zip(batch['records'],expected_group['methods']):
                    assert r['query']==expected_group['query'] and r['method']==method and r['warmup'] and r['record_kind']=='recovery'
                    assert r['process']==env['process'] and r['rows']==truth[r['query']]['rows'] and r['sha256']==truth[r['query']]['sha256']
                    validate_origin(r,out,design,manifest_path.parent,ledger)
                    extra+=1
        assert len(records)==6048
        queries=read(ROOT/'inputs'/item['dataset']/'queries.json');truth=read(ROOT/'inputs'/item['dataset']/'truth.json')
        generator=random.Random(280112+item['block']);expected=[]
        for repeat in [-1,0,1,2]:
            qs=list(queries);generator.shuffle(qs)
            for q in qs:
                methods=sorted(METHODS,key=lambda method:hashlib.sha256(f'280112:{item["block"]}:{repeat}:{q["id"]}:{method}'.encode()).digest())
                expected.extend((q['id'],method,repeat) for method in methods)
        grouped=defaultdict(list);unique=set()
        for index,(r,key) in enumerate(zip(records,expected)):
            rawcalls+=1;actual=(r['query'],r['method'],r['repeat'])
            assert actual==key and actual not in unique;unique.add(actual)
            assert r['dataset']==item['dataset'] and r['block']==item['block'] and r['call_order']==index
            assert r['warmup']==(r['repeat']<0) and r['rows']==truth[r['query']]['rows'] and r['sha256']==truth[r['query']]['sha256']
            assert r['cpu']==1 and r['io']==4 and r['contract']=='ordered_dense'
            if not fixture:
                if item['origin']=='legacy':
                    check_entry(cert['authorization']);a=read(cert['authorization']['path'])
                    assert r['process']==cert['process'] and r['time_policy']==a['time_policy']
                    assert datetime.fromisoformat(a['started_utc'])<=datetime.fromisoformat(r['started_utc'])<=datetime.fromisoformat(r['ended_utc'])<datetime.fromisoformat(a['deadline_utc'])
                else:
                    assert r['record_kind']=='scheduled'
                    validate_origin(r,out,design,manifest_path.parent,read(manifest_path.parent/'budget.json'))
            for p in PHASES:
                assert p not in r or r[p] is None or math.isfinite(r[p]) and r[p]>=0
            assert r['seconds']>0 and close(r['seconds'],r['scan_seconds']+r['normalize_seconds']+r.get('overhead_seconds',0))
            if not r['warmup']:grouped[r['dataset'],r['block'],r['query'],r['method']].append(r)
        for key,rows in grouped.items():
            assert len(rows)==3 and key not in medians
            medians[key]=sorted(rows,key=lambda row:(row['seconds'],row['repeat']))[1]
    for item in manifest['constructions']:
        cert=read(Path(item['path'])/'verification.json');assert cert['all_passed']
        for r in cert['results']:
            key=(r['dataset'],r['block'],r['method']);assert key not in builds;builds[key]=r
    assert rawcalls==24192 and len(medians)==6048 and len(builds)==204 and extra==proof['recovery_warmup_calls']
    assert excluded=={str(Path(x['path']).resolve()) for x in proof['excluded_uncommitted_groups']}
    for item in proof['excluded_uncommitted_groups']:check_entry(item)
    supplied=table(output/'request_medians.csv');assert len(supplied)==6048
    found=set()
    for r in supplied:
        key=(r['dataset'],int(r['block']),r['query'],r['method']);assert key not in found;found.add(key);chosen=medians[key]
        assert int(r['repeat'])==chosen['repeat'] and int(r['call_order'])==chosen['call_order']
        for field in PHASES:assert equal_nullable(r[field],chosen.get(field));checks+=1
    family=defaultdict(list)
    for key,r in medians.items():
        for scope in ['all',r['family']]:family[key[0],key[1],key[3],scope].append(r)
    costs={}
    for key,rows in family.items():
        xs=[r['seconds'] for r in rows];costs[key]=dict(A_ms=mean(xs)*1000,G_ms=geo(xs)*1000,B_s=math.fsum(xs))
    rows=table(output/'block_costs.csv');assert len(rows)==196
    for r in rows:
        key=(r['dataset'],int(r['block']),r['method'],r['scope'])
        for metric in ['A_ms','G_ms','B_s']:assert close(r[metric],costs[key][metric]);checks+=1
    rows=table(output/'query_summary.csv');assert len(rows)==98
    for r in rows:
        assert r['blocks']=='2';keys=[(r['dataset'],b,r['method'],r['scope']) for b in [0,1]]
        for metric in ['A_ms','G_ms','B_s']:
            xs=[costs[k][metric] for k in keys]
            for suffix,value in [('geo',geo(xs)),('arithmetic',mean(xs)),('min',min(xs)),('max',max(xs))]:assert close(r[metric+'_'+suffix],value);checks+=1
            assert all(close(a,b) for a,b in zip(json.loads(r[metric+'_blocks']),xs))
        for phase in PHASES[1:]:
            xs=[math.fsum(x[phase] for x in family[k]) if family[k][0].get(phase) is not None else None for k in keys]
            published=json.loads(r[phase+'_sum_blocks']);assert len(published)==2
            for a,b in zip(published,xs):assert a is None and b is None or a is not None and b is not None and close(a,b)
            assert equal_nullable(r[phase+'_sum_mean'],mean(xs) if xs[0] is not None else None);checks+=3
    rows=table(output/'paired_ratios.csv');assert len(rows)==420
    for r in rows:
        xs=[costs[r['dataset'],b,r['layout'],r['scope']][r['metric']]/costs[r['dataset'],b,r['baseline'],r['scope']][r['metric']] for b in [0,1]]
        assert r['blocks']=='2' and close(r['ratio'],geo(xs)) and close(r['minimum'],min(xs)) and close(r['maximum'],max(xs))
        assert int(r['faster_blocks'])==sum(x<1 for x in xs)
        assert all(close(a,b) for a,b in zip(xs,json.loads(r['ratios'])));checks+=6
    rows=table(output/'build_blocks.csv');assert len(rows)==204
    for r in rows:
        actual=builds[r['dataset'],int(r['block']),r['method']]
        for name,value in actual.items():
            if isinstance(value,(int,float)):assert close(r[name],value);checks+=1
    rows=table(output/'build_summary.csv');assert len(rows)==34
    for r in rows:
        assert r['blocks']=='6'
        for phase in BUILD_PHASES:
            xs=[builds[r['dataset'],b,r['method']][phase] for b in range(6)]
            for suffix,value in [('mean',mean(xs)),('min',min(xs)),('max',max(xs))]:assert close(r[phase+'_'+suffix],value);checks+=1
            assert all(close(a,b) for a,b in zip(json.loads(r[phase+'_blocks']),xs))
    grid={'simplex_'+''.join(map(str,w)):[x/5 for x in w] for w in itertools.product(range(6),repeat=6) if sum(w)==5};grid['equal']=[1/6]*6
    rows=table(output/'amortization_query_blocks.csv');assert len(rows)==10120
    for r in rows:
        ds,b,method,baseline=r['dataset'],int(r['query_block']),r['method'],r['baseline'];w=grid[r['scenario']];assert json.loads(r['weights'])==w
        phase='from_rnx_seconds' if baseline.endswith('_R') else 'from_crx_seconds'
        prep=mean([builds[ds,k,method][phase] for k in range(6)])
        direct=sum(w[i]*costs[ds,b,baseline,'Q'+str(i+1)]['A_ms']/1000 for i in range(6))
        layout=sum(w[i]*costs[ds,b,method,'Q'+str(i+1)]['A_ms']/1000 for i in range(6))
        for name,value in [('mean_preparation_seconds',prep),('direct_seconds_per_query',direct),('layout_seconds_per_query',layout),('saving_seconds_per_query',direct-layout)]:assert close(r[name],value);checks+=1
        assert equal_nullable(r['break_even_queries'],n_queries(prep,direct,layout));checks+=1
    rows=table(output/'amortization_summary.csv');assert len(rows)==5060;found=set()
    for r in rows:
        ds,method,baseline=r['dataset'],r['method'],r['baseline'];key=(ds,method,baseline,r['scenario']);assert key not in found;found.add(key)
        w=grid[r['scenario']];assert json.loads(r['weights'])==w and r['query_blocks']=='2' and r['build_blocks']=='6'
        phase='from_rnx_seconds' if baseline.endswith('_R') else 'from_crx_seconds';prep=[builds[ds,k,method][phase] for k in range(6)]
        direct=[sum(w[i]*costs[ds,b,baseline,'Q'+str(i+1)]['A_ms']/1000 for i in range(6)) for b in [0,1]]
        layout=[sum(w[i]*costs[ds,b,method,'Q'+str(i+1)]['A_ms']/1000 for i in range(6)) for b in [0,1]]
        assert close(r['mean_preparation_seconds'],mean(prep)) and close(r['mean_direct_seconds_per_query'],mean(direct)) and close(r['mean_layout_seconds_per_query'],mean(layout))
        assert equal_nullable(r['break_even_queries'],n_queries(mean(prep),mean(direct),mean(layout)))
        values=[n_queries(p,direct[b],layout[b]) for p in prep for b in [0,1]];finite=[x for x in values if x is not None]
        assert json.loads(r['combination_values'])==values and int(r['finite_combinations'])==len(finite)
        assert equal_nullable(r['combination_min'],min(finite) if finite else None) and equal_nullable(r['combination_max'],max(finite) if finite else None);checks+=8
    footprint=table(output/'storage_footprint.csv');assert len(footprint)==34
    inventory={(x['dataset'],x['method']):x for x in table(ROOT/'analysis/metadata_inventory/metadata_breakdown.csv')}
    for r in footprint:
        old=inventory[r['dataset'],r['method']];metric=read(ROOT/'inputs'/r['dataset']/'layout_metrics.json')[r['method']]
        assert int(r['parquet_bytes'])==int(old['parquet_bytes']) and int(r['data_files'])==metric['files'] and int(r['row_groups'])==metric['row_groups']
        assert int(r['required_metadata_bytes'])==int(old['reader_metadata_bytes']) and close(r['total_MiB'],int(old['retained_total_bytes'])/1024**2);checks+=5
    for r in table(output/'source_sizes.csv'):
        sources=read(ROOT/'inputs'/r['dataset']/'sources.json')['sources']
        assert int(r['files'])==len(sources)
        for key in ['compressed_bytes','expanded_bytes']:assert int(r[key])==sum(s['compressed_bytes' if key=='compressed_bytes' else 'source_bytes'] for s in sources)
    result=dict(all_passed=True,purpose=manifest['purpose'],raw_calls=rawcalls,formal_calls=18144,warmup_calls=6048,recovery_warmup_calls=extra,
                median_calls=6048,layout_builds=204,checks=checks,imports_primary_analyzer=False,
                source_manifest_sha256=digest(manifest_path),outputs={p.name:digest(p) for p in output.glob('*.csv')})
    (output/'independent_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='outputs'},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--allow-fixture',action='store_true')
    a=p.parse_args();verify(a.source_manifest,a.output,a.allow_fixture)
