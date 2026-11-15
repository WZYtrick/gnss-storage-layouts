"""Independent portable raw-call and supplementary-statistics verifier."""
from pathlib import Path
import csv,hashlib,itertools,json,math,random,socket
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
def close(a,b):return math.isclose(float(a),float(b),rel_tol=2e-12,abs_tol=1e-10)

def mean(xs):return math.fsum(xs)/len(xs)

def geo(xs):return math.exp(mean([math.log(x) for x in xs]))

def equal_nullable(cell,value):return cell=='' if value is None else close(cell,value)

def n_queries(prep,direct,layout):return None if direct<=layout else math.ceil(prep/(direct-layout))


def path_in_package(value):
    result=(ROOT/value).resolve()
    assert result.is_relative_to(ROOT),'Evidence path escapes package'
    return result
def checked(item):
    p=path_in_package(item['path'])
    assert p.stat().st_size==item['bytes'] and digest(p)==item['sha256'],item['path']
    return p
def validate_origin(r,b):
    begin=datetime.fromisoformat(r['started_utc']);end=datetime.fromisoformat(r['ended_utc']);assert begin<=end
    if b['origin']=='legacy':
        a=b['authorization'];assert r['process']==b['legacy_process'] and r['time_policy']==a['time_policy']
        assert datetime.fromisoformat(a['started_utc'])<=begin<=end<datetime.fromisoformat(a['deadline_utc'])
    else:
        s=b['sessions'][r['segment_id']]
        assert r['process']==s['process'] and s['cpu_pool']==1 and s['io_pool']==4
        assert r['design_sha256']==s['original_design_sha256'] and r['time_policy']==s['time_policy']
        assert datetime.fromisoformat(s['started_utc'])<=begin<=end<=datetime.fromisoformat(s['ended_utc'])
        elapsed=(end-datetime.fromisoformat(s['budget_started_utc'])).total_seconds();assert elapsed>=0
        for p in s['pauses']:
            left=datetime.fromisoformat(p['paused_utc']);right=datetime.fromisoformat(p['resumed_utc'])
            assert end<=left or begin>=right
            elapsed-=max(0.,(min(end,right)-left).total_seconds())
        assert elapsed<s['measurement_budget_seconds']

def audit_raw(manifest):
    provenance=read(path_in_package(manifest['provenance_map']))['files'];by_path={p['path']:p for p in provenance}
    assert len(by_path)==len(provenance)
    for evidence in provenance:checked(evidence)
    medians={};builds={};rawcalls=0;extra=0;blocks=set()
    for b in manifest['baselines']:
        block_key=(b['dataset'],b['block']);assert block_key not in blocks;blocks.add(block_key)
        cert=read(checked(b['certificate']));assert cert['all_passed'] and cert['formal_calls']==4536 and cert['warmup_calls']==1512
        if b['origin']=='legacy':
            records=[json.loads(x) for x in checked(b['original_stream']).read_text(encoding='utf-8').splitlines()]
        else:
            cp=read(checked(b['checkpoint']));assert cp['next_group']==864 and len(cp['commits'])==864 and len(b['groups'])==864
            records=[]
            for i,(commit,descriptor) in enumerate(zip(cp['commits'],b['groups'])):
                assert commit['path']==descriptor['path'] and commit['sha256']==by_path[descriptor['path']]['original_sha256']
                batch=read(checked(descriptor));assert batch['kind']=='scheduled' and batch['group']['index']==i and len(batch['records'])==7
                records.extend(batch['records'])
        flat=[json.loads(x) for x in checked(b['scheduled_stream']).read_text(encoding='utf-8').splitlines()]
        assert flat==records and len(records)==6048
        truth=read(ROOT/'inputs'/b['dataset']/'truth.json');queries=read(ROOT/'inputs'/b['dataset']/'queries.json')
        rng=random.Random(280112+b['block']);expected=[]
        for repeat in [-1,0,1,2]:
            order=list(queries);rng.shuffle(order)
            for q in order:
                methods=sorted(METHODS,key=lambda m:hashlib.sha256(f'280112:{b["block"]}:{repeat}:{q["id"]}:{m}'.encode()).digest())
                expected.extend((q['id'],m,repeat) for m in methods)
        grouped=defaultdict(list);seen=set();last=None;families={q['id']:q['family'] for q in queries}
        for i,(r,expected_call) in enumerate(zip(records,expected)):
            key=(r['query'],r['method'],r['repeat']);assert key==expected_call and key not in seen;seen.add(key)
            assert r['dataset']==b['dataset'] and r['block']==b['block'] and r['call_order']==i
            assert r['family']==families[r['query']] and r['warmup']==(r['repeat']<0)
            assert r['cpu']==1 and r['io']==4 and r['contract']=='ordered_dense'
            assert r['rows']==truth[r['query']]['rows'] and r['sha256']==truth[r['query']]['sha256']
            if b['origin']!='legacy':assert r['record_kind']=='scheduled'
            assert r['seconds']>0 and math.isfinite(r['seconds'])
            assert close(r['seconds'],r['scan_seconds']+r['normalize_seconds']+r.get('overhead_seconds',0))
            for phase in PHASES:assert r.get(phase) is None or math.isfinite(r[phase]) and r[phase]>=0
            validate_origin(r,b)
            if last is not None:assert datetime.fromisoformat(r['started_utc'])>=last
            last=datetime.fromisoformat(r['ended_utc']);rawcalls+=1
            if not r['warmup']:grouped[r['dataset'],r['block'],r['query'],r['method']].append(r)
        for key,rows in grouped.items():
            assert len(rows)==3 and key not in medians;medians[key]=sorted(rows,key=lambda r:(r['seconds'],r['repeat']))[1]
        for descriptor in b['recovery_groups']:
            p=checked(descriptor);batch=read(p);g=read(p.parent.parent/'recovery_plan.json')['groups'][int(p.stem[1:])]
            assert batch['kind']=='recovery' and batch['group']==g and len(batch['records'])==7
            for r,m in zip(batch['records'],g['methods']):
                assert r['query']==g['query'] and r['method']==m and r['warmup'] and r['repeat']==-1 and r['record_kind']=='recovery'
                assert r['dataset']==b['dataset'] and r['block']==b['block'] and r['cpu']==1 and r['io']==4 and r['contract']=='ordered_dense'
                assert r['rows']==truth[r['query']]['rows'] and r['sha256']==truth[r['query']]['sha256']
                assert r['seconds']>0 and close(r['seconds'],r['scan_seconds']+r['normalize_seconds']+r.get('overhead_seconds',0))
                validate_origin(r,b);extra+=1
    assert blocks=={(ds,b) for ds in ['D1','D2'] for b in [0,1]}
    for b in manifest['constructions']:
        cert=read(checked(b['certificate']));assert cert['all_passed'];auth=b['authorization']
        assert (cert['dataset'],cert['block'])==(b['dataset'],b['block']) and cert['time_policy']==auth['time_policy']
        assert datetime.fromisoformat(auth['started_utc'])<=datetime.fromisoformat(cert['started_utc'])<=datetime.fromisoformat(cert['ended_utc'])<datetime.fromisoformat(auth['deadline_utc'])
        for r in cert['results']:
            key=(r['dataset'],r['block'],r['method']);assert key not in builds;builds[key]=r
            assert close(r['from_rnx_seconds'],r['parse_seconds']+r['build_seconds']+r['init_seconds'])
            assert close(r['from_crx_seconds'],r['from_rnx_seconds']+r['decompress_seconds'])
    for e in manifest['excluded']:checked(e)
    assert (rawcalls,extra,len(medians),len(builds))==(24192,5453,6048,204)
    return medians,builds,rawcalls,extra

def verify(manifest_path,output):
    def denied(*args,**kwargs):raise RuntimeError('Independent replay is offline')
    socket.create_connection=denied;socket.socket.connect=denied
    manifest_path=Path(manifest_path);output=Path(output);manifest=read(manifest_path);proof=read(output/'verification.json')
    assert manifest['manifest_format']==1 and manifest['purpose']=='formal' and proof['all_passed'] and proof['purpose']=='formal'
    assert manifest['query_blocks']==[0,1] and manifest['construction_blocks']==list(range(6))
    checked(proof['source_manifest']);assert digest(manifest_path)==proof['source_manifest']['sha256']
    for n,h in proof['output_hashes'].items():assert digest(output/n)==h,n
    for e in proof['inputs']:checked(e)
    medians,builds,rawcalls,extra=audit_raw(manifest);checks=0
    assert extra==proof['recovery_warmup_calls'] and manifest['excluded']==proof['excluded_uncommitted_groups']
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