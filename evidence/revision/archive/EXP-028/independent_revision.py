"""Independent raw-log recomputation of the revision analysis (stdlib only)."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from collections import defaultdict

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def csvrows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def close(a,b):return math.isclose(float(a),float(b),rel_tol=2e-12,abs_tol=1e-10)
def geo(xs):return math.prod(xs)**(1/len(xs))

def verify(campaign,output):
    campaign=Path(campaign);output=Path(output);design=read(campaign/'design.json')
    assert read(campaign/'status.json')['state']=='complete'
    previous=read(output/'verification.json')
    assert previous['all_passed'] and digest(campaign/'design.json')==previous['campaign_design']['sha256']
    for name,value in previous['output_hashes'].items():assert digest(output/name)==value
    medians={};builds={};rawcalls=0;checks=0
    for job in design['jobs']:
        root=campaign/'runs'/job['id'];cert=read(root/'verification.json')
        assert cert['all_passed'] and not (root/'failure.json').exists()
        if job['kind']=='construction':
            assert cert['purpose']=='formal'
            for r in cert['results']:
                key=(r['dataset'],int(r['block']),r['method']);assert key not in builds;builds[key]=r
            continue
        groups=defaultdict(list);unique=set()
        for line in (root/'timings.jsonl').read_text(encoding='utf-8').splitlines():
            r=json.loads(line);rawcalls+=1
            key=(r['dataset'],int(r['block']),r['query'],r['method'])
            callkey=key+(r['repeat'],);assert callkey not in unique;unique.add(callkey)
            assert r['warmup']==(r['repeat']<0)
            if not r['warmup']:groups[key].append(r)
        assert len(unique)==6048
        for key,rows in groups.items():
            assert len(rows)==3 and key not in medians
            medians[key]=sorted(rows,key=lambda r:(r['seconds'],r['repeat']))[1]
    assert rawcalls==72576 and len(medians)==18144 and len(builds)==204
    supplied=csvrows(output/'request_medians.csv');assert len(supplied)==len(medians)
    for r in supplied:
        key=(r['dataset'],int(r['block']),r['query'],r['method']);chosen=medians[key]
        assert int(r['repeat'])==chosen['repeat'] and int(r['call_order'])==chosen['call_order']
        for field in ['seconds','scan_seconds','normalize_seconds','decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']:
            assert (r[field]=='' and field not in chosen) or close(r[field],chosen[field]);checks+=1
    family=defaultdict(list)
    for (ds,b,q,method),r in medians.items():
        for scope in ['all',r['family']]:family[ds,b,method,scope].append(r)
    costs={}
    for key,rows in family.items():
        xs=[r['seconds'] for r in rows]
        costs[key]=dict(A_ms=(math.fsum(xs)/len(xs))*1000,G_ms=1000*math.exp(math.fsum(math.log(x) for x in xs)/len(xs)),B_s=sum(xs))
    blockrows=csvrows(output/'block_costs.csv');assert len(blockrows)==len(costs)
    for r in blockrows:
        key=(r['dataset'],int(r['block']),r['method'],r['scope'])
        for name in ['A_ms','G_ms','B_s']:assert close(r[name],costs[key][name]);checks+=1
    for r in csvrows(output/'query_summary.csv'):
        keys=[(r['dataset'],b,r['method'],r['scope']) for b in range(6)]
        for name in ['A_ms','G_ms','B_s']:
            xs=[costs[k][name] for k in keys]
            for suffix,value in [('geo',geo(xs)),('arithmetic',math.fsum(xs)/6),('min',min(xs)),('max',max(xs))]:
                assert close(r[name+'_'+suffix],value);checks+=1
            assert all(close(a,b) for a,b in zip(json.loads(r[name+'_blocks']),xs))
        for phase in ['scan_seconds','normalize_seconds','decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']:
            sums=[sum(t[phase] for t in family[k]) if phase in family[k][0] else None for k in keys]
            published=json.loads(r[phase+'_sum_blocks'])
            assert len(published)==6
            if sums[0] is None:assert published==[None]*6 and r[phase+'_sum_mean']==''
            else:assert all(close(a,b) for a,b in zip(sums,published)) and close(sum(sums)/6,r[phase+'_sum_mean'])
            checks+=7
    for r in csvrows(output/'paired_ratios.csv'):
        ratios=[costs[r['dataset'],b,r['layout'],r['scope']][r['metric']]/costs[r['dataset'],b,r['baseline'],r['scope']][r['metric']] for b in range(6)]
        assert close(r['ratio'],geo(ratios)) and close(r['minimum'],min(ratios)) and close(r['maximum'],max(ratios))
        assert int(r['faster_blocks'])==sum(x<1 for x in ratios)
        assert all(close(a,b) for a,b in zip(ratios,json.loads(r['ratios'])));checks+=10
    for r in csvrows(output/'build_blocks.csv'):
        actual=builds[r['dataset'],int(r['block']),r['method']]
        for name,value in actual.items():
            if isinstance(value,(int,float)):assert close(r[name],value);checks+=1
    for r in csvrows(output/'build_summary.csv'):
        for name in ['decompress_seconds','parse_seconds','build_seconds','init_seconds','from_rnx_seconds','from_crx_seconds']:
            xs=[builds[r['dataset'],b,r['method']][name] for b in range(6)]
            assert close(r[name+'_mean'],sum(xs)/6) and close(r[name+'_min'],min(xs)) and close(r[name+'_max'],max(xs))
            assert all(close(a,b) for a,b in zip(xs,json.loads(r[name+'_blocks'])));checks+=9
    amort={}
    for r in csvrows(output/'amortization_blocks.csv'):
        ds,b,method,baseline=r['dataset'],int(r['block']),r['method'],r['baseline'];w=json.loads(r['weights'])
        def cost(m):return sum(weight*costs[ds,b,m,'Q'+str(i+1)]['A_ms']/1000 for i,weight in enumerate(w))
        direct,layout=cost(baseline),cost(method)
        prep=builds[ds,b,method]['from_rnx_seconds' if baseline.endswith('_R') else 'from_crx_seconds']
        for name,value in [('direct_seconds_per_query',direct),('layout_seconds_per_query',layout),('preparation_seconds',prep),('saving_seconds_per_query',direct-layout)]:
            assert close(r[name],value);checks+=1
        n=math.ceil(prep/(direct-layout)) if direct>layout else None
        assert r['break_even_queries']==('' if n is None else str(n));checks+=1
        key=(ds,method,baseline,r['scenario']);amort.setdefault(key,[]).append((prep,direct,layout,n))
    assert len(amort)==5180
    for r in csvrows(output/'amortization_summary.csv'):
        xs=amort[r['dataset'],r['method'],r['baseline'],r['scenario']];assert len(xs)==6
        prep,direct,layout=[math.fsum(x[i] for x in xs)/6 for i in range(3)]
        for name,value in [('mean_preparation_seconds',prep),('mean_direct_seconds_per_query',direct),('mean_layout_seconds_per_query',layout)]:
            assert close(r[name],value);checks+=1
        n=math.ceil(prep/(direct-layout)) if direct>layout else None
        assert r['break_even_queries']==('' if n is None else str(n))
        ns=[x[3] for x in xs];finite=[n for n in ns if n is not None]
        assert json.loads(r['block_values'])==ns and int(r['finite_blocks'])==len(finite)
        assert r['block_min']==('' if not finite else str(min(finite))) and r['block_max']==('' if not finite else str(max(finite)));checks+=5
    result=dict(all_passed=True,raw_calls=rawcalls,median_calls=len(medians),layout_builds=len(builds),checks=checks,
                imports_primary_analyzer=False,outputs={p.name:digest(p) for p in output.glob('*.csv')})
    (output/'independent_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='outputs'},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--analysis',type=Path,required=True)
    a=p.parse_args();verify(a.campaign,a.analysis)
