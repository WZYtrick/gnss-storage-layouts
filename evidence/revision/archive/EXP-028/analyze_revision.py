"""Analyze only a complete frozen revision campaign; never substitutes smoke timings."""
from common import *
import argparse
import csv
import itertools
import math
import statistics as st
from collections import defaultdict
from datetime import datetime,timedelta,timezone

FAMILIES=['Q'+str(i) for i in range(1,7)]
PHASES=['seconds','scan_seconds','normalize_seconds','decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']

def csvout(path,rows):
    assert rows
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,separators=(',',':')) if isinstance(v,(list,dict)) else v for k,v in row.items()})

def geometric(values):return math.exp(st.fmean(math.log(v) for v in values))

def break_even(preparation,direct,layout):
    if direct<=layout:return None
    return math.ceil(preparation/(direct-layout))

def authorized(timestamp):
    t=datetime.fromisoformat(timestamp.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
    return t.hour>=1 and t.hour<8

def weights():
    mixtures=[('simplex_'+''.join(map(str,x)),[v/5 for v in x]) for x in itertools.product(range(6),repeat=6) if sum(x)==5]
    mixtures.append(('equal',[1/6]*6));assert len(mixtures)==253
    families=[(name,[int(i==j) for i in range(6)]) for j,name in enumerate(FAMILIES)]
    return mixtures+families

def analyze(campaign,output):
    campaign=Path(campaign);output=Path(output)
    design=load(campaign/'design.json');state=load(campaign/'status.json')
    assert state['state']=='complete','A complete campaign is required'
    assert len(design['jobs'])==36 and design['expected']==dict(formal_query_calls=54432,warmup_query_calls=18144,layout_builds=204)
    assert len({j['id'] for j in design['jobs']})==36
    logs=[];medians=[];build=[];formal=warm=0;code=design['runtime_code']
    for job in design['jobs']:
        root=campaign/'runs'/job['id'];cert=load(root/'verification.json')
        assert cert['all_passed'] and not (root/'failure.json').exists()
        for name,value in cert['code'].items():assert code[name]==value
        logs.append(entry(root/'verification.json'))
        if job['kind']=='construction':
            assert cert['purpose']=='formal' and cert['time_policy']=='Beijing_01_08'
            assert cert['dataset']==job['dataset'] and cert['block']==job['block']
            assert authorized(cert['ended_utc'])
            expected=set(load(ROOT/'inputs'/job['dataset']/'layouts.json'))
            assert {r['method'] for r in cert['results']}==expected
            for row in cert['results']:
                assert row['dataset']==job['dataset'] and row['block']==job['block']
                assert row['from_rnx_seconds']==row['parse_seconds']+row['build_seconds']+row['init_seconds']
                assert row['from_crx_seconds']==row['decompress_seconds']+row['from_rnx_seconds'] or math.isclose(row['from_crx_seconds'],row['decompress_seconds']+row['from_rnx_seconds'],rel_tol=1e-14)
                assert row['verified_rows']==sum(x['rows'] for x in row['full_source_hashes'])
                build.append(row)
            continue
        manifest,qs,truth,_=inputs(job['dataset']);qmap={q['id']:q for q in qs}
        env=load(root/'environment.json');process=env['process']
        assert env['cpu_pool']==1 and env['io_pool']==4
        grouped=defaultdict(list);seen=set();last_end=None
        with (root/'timings.jsonl').open(encoding='utf-8') as f:
            for order,line in enumerate(f):
                r=json.loads(line);key=(r['query'],r['method'],r['repeat'])
                assert key not in seen;seen.add(key)
                assert r['call_order']==order and r['process']==process
                assert r['dataset']==job['dataset'] and r['block']==job['block']
                assert r['contract']=='ordered_dense' and r['cpu']==1 and r['io']==4
                assert r['time_policy']=='Beijing_01_08'
                assert authorized(r['started_utc']) and authorized(r['ended_utc'])
                assert r['ended_utc']>=r['started_utc']
                if last_end is not None:assert r['started_utc']>=last_end
                last_end=r['ended_utc']
                assert r['family']==qmap[r['query']]['family']
                assert r['rows']==truth[r['query']]['rows'] and r['sha256']==truth[r['query']]['sha256']
                assert r['seconds']>0 and all(r.get(p,0)>=0 for p in PHASES)
                if r['method'] in CORE:
                    assert math.isclose(r['scan_seconds']+r['normalize_seconds'],r['seconds'],rel_tol=1e-12)
                else:
                    assert math.isclose(sum(r[p] for p in ['decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']),r['seconds'],rel_tol=1e-12)
                assert r['warmup']==(r['repeat']==-1)
                if r['warmup']:warm+=1
                else:grouped[r['query'],r['method']].append(r);formal+=1
        expected={(q['id'],m,r) for q in qs for m in METHODS for r in [-1,0,1,2]}
        assert seen==expected and cert['formal_calls']==4536 and cert['warmup_calls']==1512
        for (qid,method),rows in grouped.items():
            assert len(rows)==3
            r=sorted(rows,key=lambda x:(x['seconds'],x['repeat']))[1]
            medians.append({k:r[k] for k in ['dataset','block','query','family','method','repeat','rows','call_order','started_utc']}|
                           {p:r.get(p,None) for p in PHASES})
        logs.append(entry(root/'timings.jsonl'));logs.append(entry(root/'environment.json'))
    assert formal==54432 and warm==18144 and len(build)==204 and len(medians)==18144
    output.mkdir(parents=True,exist_ok=False)
    csvout(output/'request_medians.csv',medians)
    grouped=defaultdict(list)
    for r in medians:
        for scope in ['all',r['family']]:grouped[r['dataset'],r['block'],r['method'],scope].append(r)
    blocks=[];lookup={}
    for (dataset,block,method,scope),rows in sorted(grouped.items()):
        times=[r['seconds'] for r in rows];assert len(times)==(216 if scope=='all' else 36)
        row=dict(dataset=dataset,block=block,method=method,scope=scope,queries=len(rows),
                 A_ms=st.fmean(times)*1000,G_ms=geometric(times)*1000,B_s=sum(times))
        for phase in PHASES[1:]:
            row[phase+'_sum']=sum(r[phase] for r in rows) if rows[0][phase] is not None else None
        blocks.append(row);lookup[dataset,block,method,scope]=row
    csvout(output/'block_costs.csv',blocks)
    summary=[]
    for dataset in ['D1','D2']:
        for method in METHODS:
            for scope in ['all']+FAMILIES:
                rr=[lookup[dataset,b,method,scope] for b in range(6)]
                row=dict(dataset=dataset,method=method,scope=scope,blocks=6)
                for metric in ['A_ms','G_ms','B_s']:
                    values=[r[metric] for r in rr]
                    row.update({metric+'_geo':geometric(values),metric+'_arithmetic':st.fmean(values),metric+'_min':min(values),metric+'_max':max(values),metric+'_blocks':values})
                for phase in PHASES[1:]:
                    values=[r[phase+'_sum'] for r in rr]
                    row[phase+'_sum_mean']=st.fmean(values) if values[0] is not None else None
                    row[phase+'_sum_blocks']=values
                summary.append(row)
    csvout(output/'query_summary.csv',summary)
    paired=[]
    for dataset in ['D1','D2']:
        for scope in ['all']+FAMILIES:
            for layout in CORE:
                for baseline in METHODS[-2:]:
                    for metric in ['A_ms','G_ms','B_s']:
                        ratios=[lookup[dataset,b,layout,scope][metric]/lookup[dataset,b,baseline,scope][metric] for b in range(6)]
                        paired.append(dict(dataset=dataset,scope=scope,layout=layout,baseline=baseline,metric=metric,
                                           ratio=geometric(ratios),ratios=ratios,minimum=min(ratios),maximum=max(ratios),faster_blocks=sum(x<1 for x in ratios)))
    csvout(output/'paired_ratios.csv',paired)
    scalar_build=[{k:v for k,v in r.items() if not isinstance(v,(list,dict))} for r in build]
    csvout(output/'build_blocks.csv',scalar_build)
    groups=defaultdict(list)
    for row in build:groups[row['dataset'],row['method']].append(row)
    builds=[]
    for (dataset,method),rows in sorted(groups.items()):
        rows.sort(key=lambda x:x['block']);assert [x['block'] for x in rows]==list(range(6))
        row=dict(dataset=dataset,method=method,blocks=6)
        for field in ['decompress_seconds','parse_seconds','build_seconds','init_seconds','from_rnx_seconds','from_crx_seconds']:
            values=[r[field] for r in rows];row.update({field+'_mean':st.fmean(values),field+'_min':min(values),field+'_max':max(values),field+'_blocks':values})
        builds.append(row)
    csvout(output/'build_summary.csv',builds)
    buildmap={(r['dataset'],r['block'],r['method']):r for r in build}
    amort=[];amort_summary=[]
    for dataset in ['D1','D2']:
        for method in CORE:
            for baseline in METHODS[-2:]:
                numerator='from_rnx_seconds' if baseline.endswith('_R') else 'from_crx_seconds'
                for scenario,w in weights():
                    rows=[]
                    for b in range(6):
                        direct=sum(w[i]*lookup[dataset,b,baseline,f]['A_ms']/1000 for i,f in enumerate(FAMILIES))
                        layout=sum(w[i]*lookup[dataset,b,method,f]['A_ms']/1000 for i,f in enumerate(FAMILIES))
                        prep=buildmap[dataset,b,method][numerator]
                        row=dict(dataset=dataset,method=method,baseline=baseline,scenario=scenario,weights=w,block=b,
                                 preparation_seconds=prep,direct_seconds_per_query=direct,layout_seconds_per_query=layout,
                                 saving_seconds_per_query=direct-layout,break_even_queries=break_even(prep,direct,layout))
                        amort.append(row);rows.append(row)
                    meanprep=st.fmean(r['preparation_seconds'] for r in rows)
                    meandirect=st.fmean(r['direct_seconds_per_query'] for r in rows)
                    meanlayout=st.fmean(r['layout_seconds_per_query'] for r in rows)
                    ns=[r['break_even_queries'] for r in rows];finite=[n for n in ns if n is not None]
                    amort_summary.append(dict(dataset=dataset,method=method,baseline=baseline,scenario=scenario,weights=w,
                          mean_preparation_seconds=meanprep,mean_direct_seconds_per_query=meandirect,
                          mean_layout_seconds_per_query=meanlayout,break_even_queries=break_even(meanprep,meandirect,meanlayout),
                          finite_blocks=len(finite),block_min=min(finite) if finite else None,block_max=max(finite) if finite else None,block_values=ns))
    csvout(output/'amortization_blocks.csv',amort);csvout(output/'amortization_summary.csv',amort_summary)
    dump(output/'verification.json',dict(all_passed=True,formal_calls=formal,warmup_calls=warm,layout_builds=len(build),median_calls=len(medians),
          campaign_design=entry(campaign/'design.json'),inputs=logs,output_hashes={p.name:sha(p) for p in output.glob('*.csv')},
          independent_verification_required=True,purpose='post-review supplemental measurement'))
    print('Complete revision campaign analyzed; independent verification is still required.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();analyze(a.campaign,a.output)
