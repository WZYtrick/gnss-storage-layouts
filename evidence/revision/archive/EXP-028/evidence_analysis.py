"""Recompute reviewer-requested tables from preserved development evidence."""
from common import *
import csv
import itertools
import math
import statistics as st
from collections import defaultdict, Counter

OUT = ROOT/'analysis/existing_evidence'
DEV = ROOT/'evidence/development_combined_20261109'
HOLD = ROOT/'evidence/d2_combined_20261110'

def readcsv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))

def writecsv(path, rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if not rows: raise ValueError('Refuse empty output '+str(path))
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False,separators=(',',':')) if isinstance(v,(list,dict)) else v for k,v in row.items()})

def gm(xs): return math.exp(st.fmean(map(math.log,xs)))

def family_metrics(folder,dataset):
    groups=defaultdict(list)
    keys=set()
    for row in readcsv(folder/'request_medians.csv'):
        if row['module']!='E1':continue
        assert row['dataset']==dataset
        key=(int(row['cpu']),row['contract'],int(row['block']),row['method'],row['family'])
        unique=key+(row['query'],)
        assert unique not in keys;keys.add(unique)
        groups[key].append(float(row['seconds'])*1000)
    metrics={}
    for key,values in groups.items():
        assert len(values)==36
        metrics[key]=dict(A_ms=st.fmean(values),G_ms=gm(values))
    checks=0
    for row in readcsv(folder/'block_metrics.csv'):
        if row['module']!='E1' or row['scope'] not in ['Q'+str(i) for i in range(1,7)]:continue
        key=(int(row['cpu']),row['contract'],int(row['block']),row['method'],row['scope'])
        for metric in ['A_ms','G_ms']:
            assert math.isclose(metrics[key][metric],float(row[metric]),rel_tol=2e-12,abs_tol=1e-10)
            checks+=1
    return metrics,checks

def select(costs, sizes, threshold):
    low=min(costs.values())
    near=[method for method,cost in costs.items() if cost<=low*(1+threshold)+1e-12]
    return min(near,key=lambda method:(sizes[method]['total_bytes'],sizes[method]['files'],method))

def qualify(center, faster_blocks, space_ratio, speed=.05, near=.03, space=.05):
    return (center<=1-speed+1e-12 and faster_blocks>=5,
            center<=1+near+1e-12 and space_ratio<=1-space+1e-12)

def centers(metrics,cpu,contract,metric,weights):
    result={}
    blocks={}
    for method in CORE:
        values=[]
        for block in range(6):
            family=[metrics[cpu,contract,block,method,'Q'+str(i+1)][metric] for i in range(6)]
            cost=sum(w*v for w,v in zip(weights,family)) if metric=='A_ms' else math.exp(sum(w*math.log(v) for w,v in zip(weights,family)))
            values.append(cost)
        result[method]=gm(values);blocks[method]=values
    return result,blocks

def replay_predictions():
    dev,dc=family_metrics(DEV,'D1');hold,hc=family_metrics(HOLD,'D2')
    predictions=load(ROOT/'evidence/freezes/D2_predictions.json')
    sizes=load(ROOT/'inputs/D1/layout_metrics.json')
    expected={tuple(x/5 for x in ints) for ints in itertools.product(range(6),repeat=6) if sum(ints)==5}
    expected.add(tuple([1/6]*6));assert len(expected)==253
    actual={tuple(p['weights']) for p in predictions['predictions'] if p['grid']=='simplex'}
    assert actual==expected
    old={(r['cpu'],r['contract'],r['metric'],r['grid'],r['scenario']):r for r in readcsv(ROOT/'evidence/synthesis_20261110/frozen_rule_weights.csv')}
    rows=[];checks=dc+hc
    for p in predictions['predictions']:
        cpu,contract,metric,w=p['cpu'],p['contract'],p['metric'],p['weights']
        dc,db=centers(dev,cpu,contract,metric,w)
        hc,hb=centers(hold,cpu,contract,metric,w)
        for method in CORE:
            assert math.isclose(dc[method],p['D1_centers'][method],rel_tol=2e-12,abs_tol=1e-10);checks+=1
        frozen=select(dc,sizes,.03)
        assert frozen==p['recommended'];checks+=1
        best=min(hc,key=lambda method:(hc[method],method))
        previous=old[str(cpu),contract,metric,p['grid'],p['scenario']]
        assert frozen==previous['frozen_selected'] and best==previous['observed_best']
        assert math.isclose(hc[frozen]/hc[best]-1,float(previous['relative_cost_penalty']),abs_tol=1e-10);checks+=3
        for threshold in [.01,.03,.05]:
            chosen=select(dc,sizes,threshold)
            rows.append(dict(cpu=cpu,contract=contract,metric=metric,grid=p['grid'],scenario=p['scenario'],
                             threshold=threshold,weights=w,selected=chosen,frozen03=frozen,observed_best=best,
                             D1_centers=dc,D2_centers=hc,D1_blocks=db,D2_blocks=hb,
                             penalty=hc[chosen]/hc[best]-1,changed=chosen!=frozen))
    writecsv(OUT/'rule_replay.csv',rows)
    summary=[]
    for grid,threshold in itertools.product(['simplex','alpha'],[.01,.03,.05]):
        group=[r for r in rows if r['grid']==grid and r['threshold']==threshold]
        assert len(group)==(3036 if grid=='simplex' else 1224)
        summary.append(dict(grid=grid,threshold=threshold,scenarios=len(group),mean_penalty=st.fmean(r['penalty'] for r in group),
                            maximum_penalty=max(r['penalty'] for r in group),changed=sum(r['changed'] for r in group),
                            within01=sum(r['penalty']<=.01+1e-12 for r in group),within03=sum(r['penalty']<=.03+1e-12 for r in group),
                            within05=sum(r['penalty']<=.05+1e-12 for r in group)))
    writecsv(OUT/'rule_sensitivity.csv',summary)
    return dict(checks=checks,predictions=len(predictions['predictions']),simplex_scenarios=3036,summary=summary)

def candidate_sensitivity():
    source=readcsv(DEV/'qualification.csv')
    rows=[];summary=[]
    settings=[('primary',.05,.03,.05)]
    settings += [('speed',v,.03,.05) for v in [.01,.03,.05,.10]]
    settings += [('near',.05,v,.05) for v in [.01,.03,.05]]
    settings += [('storage',.05,.03,v) for v in [.01,.03,.05,.10]]
    for axis,speed,near,space in settings:
        qualified=set()
        for item in source:
            ratios=json.loads(item['ratios']);center=gm(ratios)
            assert math.isclose(center,float(item['center_ratio']),rel_tol=1e-12)
            same=sum(x<1 for x in ratios);assert same==int(item['below_one'])
            fast,small=qualify(center,same,float(item['space_ratio']),speed,near,space)
            if fast or small:qualified.add(item['candidate'])
            rows.append(dict(axis=axis,speed_threshold=speed,near_threshold=near,storage_threshold=space,
                             module=item['module'],cpu=int(item['cpu']),contract=item['contract'],metric=item['metric'],
                             candidate=item['candidate'],core_reference=item['core_reference'],ratios=ratios,
                             center_ratio=center,faster_blocks=same,space_ratio=float(item['space_ratio']),
                             qualifies=fast or small,passes_speed=fast,passes_space=small))
        summary.append(dict(axis=axis,speed_threshold=speed,near_threshold=near,storage_threshold=space,
                            assessed_cells=len(source),qualified=sorted(qualified),count=len(qualified)))
    writecsv(OUT/'candidate_sensitivity_cells.csv',rows)
    writecsv(OUT/'candidate_sensitivity_summary.csv',summary)
    return summary

def storage_and_roles():
    storage=[];roles=[];hashsets={};sources=[]
    for dataset in ['D0m','D1','D2','D2m']:
        m,qs,truth,paths=inputs(dataset)
        metrics=load(ROOT/'inputs'/dataset/'layout_metrics.json')
        hashsets[dataset]={x['source_sha256'] for x in m['sources']}
        roles.append(dict(dataset=dataset,stations=list(dict.fromkeys(x['station'] for x in m['sources'])),
                          dates=sorted({x['date'] for x in m['sources']}),files=len(m['sources']),records=sum(x['records'] for x in m['sources']),
                          queries=len(qs),empty_queries=sum(truth[q['id']]['rows']==0 for q in qs),
                          compressed_bytes=sum(x['compressed_bytes'] for x in m['sources']),source_bytes=sum(x['source_bytes'] for x in m['sources'])))
        for item in m['sources']:
            sources.append(dict(dataset=dataset,source_id=item['id'],station=item['station'],date=item['date'],
                                file=Path(item['source_file']).name,compressed_file=Path(item['compressed_file']).name,
                                source_sha256=item['source_sha256'],compressed_sha256=item['compressed_sha256'],url=item['url']))
        for method,values in metrics.items():
            directory=Path(paths[method]);files=[x for x in directory.iterdir() if x.is_file()]
            parquet=[x for x in files if x.suffix=='.parquet']
            total=sum(p.stat().st_size for p in files);pb=sum(p.stat().st_size for p in parquet)
            assert total==values['total_bytes'] and pb==values['parquet_bytes'] and len(parquet)==values['files']
            storage.append(dict(dataset=dataset,method=method,data_files=len(parquet),metadata_files=len(files)-len(parquet),
                                all_files=len(files),row_groups=values['row_groups'],records=values['rows'],parquet_bytes=pb,
                                metadata_bytes=total-pb,total_bytes=total,total_mib=total/2**20))
    training=[load(p) for p in sorted((ROOT/'evidence/training').glob('*_0*.json'))]
    assert len(training)==6 and sum(x['records'] for x in training)==907759
    hashsets['training']={x['source_sha256'] for x in training}
    for ds in ['D0m','D1','D2']:
        assert not hashsets['training']&hashsets[ds]
    assert not hashsets['D0m']&hashsets['D1'] and not hashsets['D0m']&hashsets['D2'] and not hashsets['D1']&hashsets['D2']
    assert hashsets['D2m']<=hashsets['D2']
    roles.insert(0,dict(dataset='training',stations=sorted({x['station'] for x in training}),dates=sorted({s.source_date(x).isoformat() for x in training}),files=6,records=907759,queries=36,empty_queries=None,compressed_bytes=None,source_bytes=None))
    selection=load(ROOT/'evidence/training/selection.json')
    assert selection['selected']=={'W':'W_time_8192','C':'C_time_8192','T':'T_120m','F':'F_120m'}
    assert selection['test_data_not_used']
    writecsv(OUT/'layout_storage.csv',storage);writecsv(OUT/'dataset_roles.csv',roles);writecsv(OUT/'source_inventory.csv',sources)
    dump(OUT/'dataset_separation.json',dict(all_passed=True,training_source_hashes=sorted(hashsets['training']),
         selection_sha256=sha(ROOT/'evidence/training/selection.json'),selection_frozen_utc=dt.datetime.fromtimestamp(selection['selection_frozen_at_unix'],dt.timezone.utc).isoformat(),
         disjoint_groups=['training','D0m','D1','D2'],D2m_is_D2_subset=True))
    return roles

def threads():
    rows=[r for r in readcsv(ROOT/'evidence/synthesis_20261110/costs.csv') if r['module']=='E1' and r['evidence']=='D1' and r['scope']=='all']
    assert len(rows)==60
    writecsv(OUT/'D1_thread_costs.csv',rows)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    roles=storage_and_roles();threads();candidates=candidate_sensitivity();rule=replay_predictions()
    dump(OUT/'verification.json',dict(created=utc(),all_passed=True,role_checks=True,candidate_settings=len(candidates),
         rule=rule,inputs={str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'evidence').rglob('*.csv'))},
         outputs={p.name:sha(p) for p in sorted(OUT.glob('*.csv'))}))
    print(json.dumps(dict(all_passed=True,rule=rule,candidates=candidates),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
