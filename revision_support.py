"""Portable provenance and call reconstruction for revision results."""
from pathlib import Path
from datetime import datetime,timezone
import csv,hashlib,json,math,random,socket
from collections import defaultdict

ROOT=Path(__file__).resolve().parent
CORE=['W','C','Cstar','T','F'];METHODS=CORE+['GeoRinex_R','GeoRinex_H']
def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def entry(p):
    p=Path(p).resolve()
    return dict(path=p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p),bytes=p.stat().st_size,sha256=sha(p))
def resolve(path):
    p=(ROOT/path).resolve()
    if not p.is_relative_to(ROOT):raise ValueError('Evidence must resolve inside the package')
    return p
def checked(item):
    p=resolve(item['path'])
    if p.stat().st_size!=item['bytes'] or sha(p)!=item['sha256']:raise ValueError('Evidence hash mismatch: '+item['path'])
    return p
def inputs(ds):return tuple(load(ROOT/'inputs'/ds/(n+'.json')) for n in ['sources','queries','truth','layouts'])
def offline():
    def denied(*args,**kwargs):raise RuntimeError('Replay is offline; network access is disabled')
    socket.create_connection=denied;socket.socket.connect=denied
def schedule(queries,block):
    generator=random.Random(280112+block);groups=[]
    for repeat in [-1,0,1,2]:
        order=list(queries);generator.shuffle(order)
        for q in order:
            methods=sorted(METHODS,key=lambda m:hashlib.sha256(f'280112:{block}:{repeat}:{q["id"]}:{m}'.encode()).digest())
            groups.append(dict(index=len(groups),query=q['id'],repeat=repeat,methods=methods))
    return groups
def timing(row):
    assert math.isfinite(row['seconds']) and row['seconds']>0
    for k in ['scan_seconds','normalize_seconds','decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']:
        assert row.get(k) is None or math.isfinite(row[k]) and row[k]>=0
    assert math.isclose(row['seconds'],row['scan_seconds']+row['normalize_seconds']+row.get('overhead_seconds',0),rel_tol=1e-8,abs_tol=1e-7)
def origin(row,block):
    begin,end=map(datetime.fromisoformat,[row['started_utc'],row['ended_utc']]);assert begin<=end
    if block['origin']=='legacy':
        a=block['authorization'];assert row['process']==block['legacy_process'] and row['time_policy']==a['time_policy']
        assert datetime.fromisoformat(a['started_utc'])<=begin<=end<datetime.fromisoformat(a['deadline_utc'])
        return
    session=block['sessions'][row['segment_id']]
    assert row['process']==session['process'] and session['cpu_pool']==1 and session['io_pool']==4
    assert row['design_sha256']==session['original_design_sha256'] and row['time_policy']==session['time_policy']
    assert datetime.fromisoformat(session['started_utc'])<=begin<=end<=datetime.fromisoformat(session['ended_utc'])
    paused=0.
    for p in session['pauses']:
        left=datetime.fromisoformat(p['paused_utc']);right=datetime.fromisoformat(p['resumed_utc'])
        assert end<=left or begin>=right
        paused+=max(0.,(min(end,right)-left).total_seconds())
    assert datetime.fromisoformat(session['budget_started_utc'])<=begin
    assert (end-datetime.fromisoformat(session['budget_started_utc'])).total_seconds()-paused<session['measurement_budget_seconds']
def collect(manifest_path):
    manifest=load(manifest_path);assert manifest['manifest_format']==1 and manifest['purpose']=='formal'
    assert manifest['query_blocks']==[0,1] and manifest['construction_blocks']==list(range(6))
    provenance=load(resolve(manifest['provenance_map']))['files'];provenance_map={x['path']:x for x in provenance}
    assert len(provenance_map)==len(provenance)
    for item in provenance:checked(item)
    medians=[];builds=[];raw_count=extra=0;logs=[];seen_blocks=set()
    for b in manifest['baselines']:
        key=(b['dataset'],b['block']);assert key not in seen_blocks;seen_blocks.add(key)
        cert=load(checked(b['certificate']));assert cert['all_passed'] and cert['formal_calls']==4536 and cert['warmup_calls']==1512
        _,queries,truth,_=inputs(b['dataset']);qmap={q['id']:q for q in queries};plan=schedule(queries,b['block'])
        p=checked(b['scheduled_stream']);logs.append(b['scheduled_stream'])
        records=[json.loads(line) for line in p.read_text(encoding='utf-8').splitlines()]
        assert len(records)==6048;raw_count+=len(records)
        if b['origin']=='legacy':
            original=[json.loads(line) for line in checked(b['original_stream']).read_text(encoding='utf-8').splitlines()]
            assert original==records
        else:
            cp=load(checked(b['checkpoint']));assert cp['next_group']==864 and len(cp['commits'])==len(b['groups'])==864
            for i,(commit,ref) in enumerate(zip(cp['commits'],b['groups'])):
                assert commit['path']==ref['path'] and commit['sha256']==provenance_map[ref['path']]['original_sha256']
                g=load(checked(ref));assert g['kind']=='scheduled' and g['group']==plan[i] and g['records']==records[i*7:i*7+7]
            for ref in b['recovery_groups']:
                path=checked(ref);g=load(path);assert g['kind']=='recovery' and len(g['records'])==7
                recovery_plan=load(path.parent.parent/'recovery_plan.json')['groups'];expected=recovery_plan[int(path.stem[1:])]
                assert g['group']==expected
                for r,m in zip(g['records'],expected['methods']):
                    assert r['query']==expected['query'] and r['method']==m and r['record_kind']=='recovery' and r['warmup'] and r['repeat']==-1
                    assert r['dataset']==b['dataset'] and r['block']==b['block'] and r['cpu']==1 and r['io']==4 and r['contract']=='ordered_dense'
                    assert r['rows']==truth[r['query']]['rows'] and r['sha256']==truth[r['query']]['sha256'];timing(r);origin(r,b);extra+=1
        grouped=defaultdict(list);last_end=None;seen=set()
        for i,r in enumerate(records):
            g=plan[i//7];actual=(r['query'],r['method'],r['repeat'])
            assert actual==(g['query'],g['methods'][i%7],g['repeat']) and actual not in seen;seen.add(actual)
            assert r['dataset']==b['dataset'] and r['block']==b['block'] and r['call_order']==i
            assert r['warmup']==(r['repeat']<0) and r['family']==qmap[r['query']]['family']
            assert r['cpu']==1 and r['io']==4 and r['contract']=='ordered_dense'
            assert r['rows']==truth[r['query']]['rows'] and r['sha256']==truth[r['query']]['sha256']
            if b['origin']!='legacy':assert r['record_kind']=='scheduled'
            timing(r);origin(r,b)
            if last_end:assert datetime.fromisoformat(r['started_utc'])>=last_end
            last_end=datetime.fromisoformat(r['ended_utc'])
            if not r['warmup']:grouped[r['query'],r['method']].append(r)
        for rows in grouped.values():
            assert len(rows)==3;row=sorted(rows,key=lambda x:(x['seconds'],x['repeat']))[1]
            fields=['dataset','block','query','family','method','repeat','rows','call_order','started_utc']
            phases=['seconds','scan_seconds','normalize_seconds','decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']
            medians.append({k:row[k] for k in fields}|{k:row.get(k) for k in phases})
    seen=set()
    for item in manifest['constructions']:
        cert=load(checked(item['certificate']));assert cert['all_passed'];auth=item['authorization']
        assert (cert['dataset'],cert['block'])==(item['dataset'],item['block']) and cert['time_policy']==auth['time_policy']
        assert datetime.fromisoformat(auth['started_utc'])<=datetime.fromisoformat(cert['started_utc'])<=datetime.fromisoformat(cert['ended_utc'])<datetime.fromisoformat(auth['deadline_utc'])
        for r in cert['results']:
            key=(r['dataset'],r['block'],r['method']);assert key not in seen;seen.add(key)
            assert math.isclose(r['from_rnx_seconds'],r['parse_seconds']+r['build_seconds']+r['init_seconds'],rel_tol=1e-10,abs_tol=1e-7)
            assert math.isclose(r['from_crx_seconds'],r['from_rnx_seconds']+r['decompress_seconds'],rel_tol=1e-10,abs_tol=1e-7)
            builds.append(r)
    for item in manifest['excluded']:checked(item)
    assert seen_blocks=={(d,b) for d in ['D1','D2'] for b in [0,1]}
    assert (raw_count,extra,len(medians),len(builds))==(24192,5453,6048,204)
    return manifest,medians,builds,dict(recovery_calls=extra,inputs=logs+[entry(resolve(manifest['provenance_map']))])
