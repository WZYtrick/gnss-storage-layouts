"""Check both standard-parser paths against frozen independent raw truth."""
from common import *
import argparse
import gc
import time
import traceback
from georinex_reader import GeoRinexReader

def run(mode):
    names = ['common.py','storage.py','reader.py','georinex_reader.py','validate_baseline.py']
    code = {name:sha(ROOT/name) for name in names}
    version = hashlib.sha256(json.dumps(code,sort_keys=True).encode()).hexdigest()[:12]
    folder = ROOT/'validation'/(mode+'_'+version)
    folder.mkdir(parents=True,exist_ok=True)
    started = time.perf_counter()
    records = []
    for dataset in ['D1','D2']:
        m,qs,truth,_ = inputs(dataset)
        if mode=='smoke':
            chosen = []
            for family in ['Q1','Q2','Q3','Q4','Q5','Q6']:
                candidates = [q for q in qs if q['family']==family and truth[q['id']]['rows']>0]
                # Pick largest returned shape, independent of observed timings.
                chosen.append(max(candidates,key=lambda q:truth[q['id']]['rows']*len(q['codes'])))
            chosen.append(next(q for q in qs if truth[q['id']]['rows']==0))
            qs = chosen
        for method in ['GeoRinex_R','GeoRinex_H']:
            reader = GeoRinexReader(m,compressed=method.endswith('_H'))
            for q in qs:
                dest = folder/(dataset+'_'+method+'_'+q['id']+'.json')
                if dest.exists():
                    old = load(dest)
                    if old['code'] != code:
                        raise RuntimeError('Version changed: retain this validation and use a new directory')
                    records.append(old)
                    continue
                try:
                    result,stats = reader.query(q)
                    actual = dict(rows=len(result['rid']),sha256=s.fingerprint(result))
                    expected = truth[q['id']]
                    assert actual['rows']==expected['rows'] and actual['sha256']==expected['sha256'], (dataset,method,q['id'],actual,expected)
                    del result
                    record = dict(dataset=dataset,method=method,query=q['id'],family=q['family'],
                                  contract='ordered_dense',purpose='correctness_and_budget_only',
                                  utc=utc(),all_passed=True,code=code,**actual,**stats)
                    dump(dest,record);records.append(record)
                    print(dataset,method,q['id'],actual['rows'],round(stats['seconds'],3),'s OK',flush=True)
                    gc.collect()
                except BaseException:
                    dump(folder/'failure.json',dict(utc=utc(),dataset=dataset,method=method,query=q['id'],traceback=traceback.format_exc()))
                    raise
    dump(folder/'verification.json',dict(all_passed=True,mode=mode,queries=len(records),
                                       elapsed_seconds=time.perf_counter()-started,code=code,
                                       records=[dict(dataset=x['dataset'],method=x['method'],query=x['query'],seconds=x['seconds']) for x in records]))
    dump(ROOT/'validation'/(mode+'_latest.json'),dict(directory=str(folder),version=version,code=code))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['smoke','all']);run(p.parse_args().mode)
