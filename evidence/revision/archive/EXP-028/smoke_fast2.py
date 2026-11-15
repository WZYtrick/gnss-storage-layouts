"""Short real-data offline check; its calls are excluded from the formal campaign."""
from fast2_support import *
from fast2_run import GroupExecutor
from reader import Reader,configure_pools
from georinex_reader import GeoRinexReader
import campaign


def main():
    deny_network();campaign.guard(load(OLD/'design.json'))
    started=utc();m,queries,truth,paths=inputs('D2')
    q=next(q for q in queries if q['family']=='Q2' and truth[q['id']]['rows']>0)
    out=ROOT/'validation'/('fast2_real_smoke_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir();groups=schedule([q],0);job=dict(dataset='D2',block=0,id='REAL_DATA_SMOKE_NOT_FORMAL')
    state=dict(calls=0,paused=False);configure_pools(1,4)
    def readers():
        result={method:Reader(Path(paths[method]),m,cpu=1) for method in CORE}
        result.update(GeoRinex_R=GeoRinexReader(m),GeoRinex_H=GeoRinexReader(m,compressed=True))
        class Observed:
            def __init__(self,base):self.base=base
            def query(self,request,contract):
                actual,stats=self.base.query(request,contract);state['calls']+=1
                if state['calls']==3:state['paused']=True
                return actual,stats
        return {name:Observed(reader) for name,reader in result.items()}
    def engine(name):
        seg=out/'segments'/name;seg.mkdir(parents=True)
        return GroupExecutor(out,seg,job,[q],truth,readers(),process_identity(),'SMOKE_NOT_FORMAL',
                             lambda:None,pause_check=lambda:state['paused'])
    first=engine('seg_0001')
    cp,paused=first.scheduled(groups,checkpoint(out),lambda *args:None)
    assert paused and cp['next_group']==1 and state['calls']==7
    del first
    state['paused']=False;second=engine('seg_0002');recovery=recovery_schedule(groups,cp['next_group'])
    assert len(recovery)==1
    for index,group in enumerate(recovery):second.group(group,'recovery',index)
    assert checkpoint(out)['next_group']==1
    cp,paused=second.scheduled(groups,validate_checkpoint(out,groups),lambda *args:None)
    assert not paused and cp['next_group']==4 and state['calls']==35
    rows=[r for item in cp['commits'] for r in load(item['path'])['records']]
    assert [(r['query'],r['repeat'],r['method']) for r in rows]==[(g['query'],g['repeat'],method) for g in groups for method in g['methods']]
    assert sum(not r['warmup'] for r in rows)==21
    save(ROOT/'validation/fast2_smoke.json',dict(all_passed=True,purpose='real-data functional smoke, excluded from formal measurements',
         started_utc=started,ended_utc=utc(),dataset='D2',query=q['id'],rows=truth[q['id']]['rows'],offline=True,
         scheduled_calls=28,recovery_warmup_calls=7,received_pause_after_method=3,stopped_after_method=7,
         directory=str(out),fast_code={name:sha(ROOT/name) for name in FAST_FILES}))
    print('Real D2 offline smoke passed: 35 calls; seven-method pause and recovery; excluded from formal data.',flush=True)


if __name__=='__main__':main()
