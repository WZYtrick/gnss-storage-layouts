"""Independent raw-call and rule audit; imports no primary analysis module."""
from pathlib import Path
import csv
import json
import hashlib
import math
import statistics
from collections import defaultdict

ROOT=Path(__file__).resolve().parent
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def csvrows(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def close(a,b):return math.isclose(float(a),float(b),rel_tol=2e-11,abs_tol=1e-10)

def main(analysis_root=None):
    analysis_root=Path(analysis_root) if analysis_root else ROOT/'analysis/existing_evidence'
    checks=0
    for item in read(ROOT/'evidence/raw_manifest.json'):
        p=ROOT/item['path'];assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'];checks+=1
    median_index={}
    for label,folder in [('D1','development_combined_20261109'),('D2','d2_combined_20261110')]:
        for r in csvrows(ROOT/'evidence'/folder/'request_medians.csv'):
            if r['module']=='E4':continue
            key=(label,r['cell'],int(r['block']),r['method'],r['query'])
            assert key not in median_index;median_index[key]=r
    visited=set();families=defaultdict(list);checked_calls=0
    for run in read(ROOT/'evidence/raw_runs.json'):
        if run['module']=='E4':continue
        groups=defaultdict(list)
        with (ROOT/run['files']['timings.jsonl']).open(encoding='utf-8') as f:
            for line in f:
                r=json.loads(line);checked_calls+=1
                assert r['cell']==run['cell'] and r['block']==run['block'];checks+=1
                if r['warmup']:continue
                groups[r['method'],r['query']].append(r)
        for (method,query),calls in groups.items():
            assert len(calls)==3 and sorted(r['repeat'] for r in calls)==[0,1,2]
            timing='end_to_end_seconds' if run['module']=='E5' else 'seconds'
            median=sorted(calls,key=lambda r:(r[timing],r['repeat']))[1]
            key=(run['evidence'],run['cell'],run['block'],method,query)
            expected=median_index[key];visited.add(key)
            assert int(expected['representative_round'])==median['repeat']
            assert close(expected['seconds'],median[timing]);checks+=3
            for col in ['scan_seconds','normalize_seconds']:
                assert close(expected[col],median[col]);checks+=1
            assert expected['source_run_id']==run['source_run_id'] and expected['time_policy']==run['time_policy'];checks+=2
            if run['module']=='E1':
                families[run['evidence'],median['cpu'],median['contract'],run['block'],method,median['family']].append(median['seconds']*1000)
    assert visited==set(median_index)
    local={}
    for k,xs in families.items():
        assert len(xs)==36
        local[k,'A_ms']=sum(xs)/len(xs)
        local[k,'G_ms']=math.exp(sum(math.log(x) for x in xs)/len(xs))
    sizes=read(ROOT/'inputs/D1/layout_metrics.json');summary=defaultdict(list);changes=defaultdict(int)
    replay=csvrows(analysis_root/'rule_replay.csv')
    for r in replay:
        w=json.loads(r['weights']);cost={}
        for label in ['D1','D2']:
            for method in ['W','C','Cstar','T','F']:
                blocks=[]
                for b in range(6):
                    vals=[local[(label,int(r['cpu']),r['contract'],b,method,'Q'+str(i+1)),r['metric']] for i in range(6)]
                    weighted=sum(a*v for a,v in zip(w,vals)) if r['metric']=='A_ms' else math.exp(sum(a*math.log(v) for a,v in zip(w,vals)))
                    blocks.append(weighted)
                cost[label,method]=math.prod(blocks)**(1/6)
        minimum=min(cost['D1',m] for m in sizes)
        near=[m for m in sizes if cost['D1',m]<=minimum*(1+float(r['threshold']))+1e-12]
        chosen=sorted(near,key=lambda m:(sizes[m]['total_bytes'],sizes[m]['files'],m))[0]
        penalty=cost['D2',chosen]/min(cost['D2',m] for m in sizes)-1
        assert chosen==r['selected'] and close(penalty,r['penalty']);checks+=2
        summary[r['grid'],r['threshold']].append(penalty)
        changes[r['grid'],r['threshold']]+=chosen!=r['frozen03']
    for r in csvrows(analysis_root/'rule_sensitivity.csv'):
        vals=summary[r['grid'],r['threshold']]
        assert len(vals)==int(r['scenarios']) and close(sum(vals)/len(vals),r['mean_penalty']) and close(max(vals),r['maximum_penalty'])
        assert changes[r['grid'],r['threshold']]==int(r['changed']);checks+=4
    for r in csvrows(analysis_root/'candidate_sensitivity_cells.csv'):
        ratios=json.loads(r['ratios']);ratio=math.prod(ratios)**(1/6)
        passes=(ratio<=1-float(r['speed_threshold'])+1e-12 and sum(x<1 for x in ratios)>=5) or (ratio<=1+float(r['near_threshold'])+1e-12 and float(r['space_ratio'])<=1-float(r['storage_threshold'])+1e-12)
        assert passes==(r['qualifies']=='True') and close(ratio,r['center_ratio']);checks+=2
    result=dict(all_passed=True,imports_primary_analysis=False,checks=checks,raw_calls_checked=checked_calls,
                median_calls_checked=len(visited),threshold_scenarios_checked=len(replay),
                output_hashes={p.name:sha(p) for p in sorted(analysis_root.glob('*.csv'))})
    out=ROOT/'validation/independent_existing_evidence.json' if analysis_root==ROOT/'analysis/existing_evidence' else analysis_root/'independent_verification.json'
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='output_hashes'},indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--analysis-root',type=Path)
    main(parser.parse_args().analysis_root)
