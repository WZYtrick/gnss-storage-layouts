"""Local, durable controls and evidence for the two-block revision profile."""
from common import *
import contextlib
import datetime as dt
import msvcrt
import random
import socket
import time
import uuid
import psutil

PROFILE = 'revision_fast2_20261113'
RUN = ROOT / 'campaigns' / PROFILE
OLD = ROOT / 'campaigns/revision_v1_20261112'
POLICY = 'explicit_user_fast2_24h_active'
FAST_FILES = ['fast2_support.py', 'fast2_run.py', 'fast2_analysis.py', 'fast2_verify.py', 'fast2_report.py']
_base_dump = dump


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    with tmp.open('x', encoding='utf-8') as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    for attempt in range(100):
        try:
            os.replace(tmp, path); return
        except PermissionError:
            if attempt == 99: raise
            time.sleep(.1)


@contextlib.contextmanager
def lock(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try: yield
        finally:
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def deny_network():
    def denied(*args, **kwargs): raise RuntimeError('FAST2_OFFLINE: outbound network disabled')
    socket.create_connection = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied


def process_identity():
    p = psutil.Process()
    return dict(pid=p.pid, created=p.create_time(), executable=p.exe())


def alive(item):
    if not item: return False
    try: return abs(psutil.Process(item['pid']).create_time() - item['created']) < .001
    except psutil.NoSuchProcess: return False


def elapsed_active(ledger, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    start = dt.datetime.fromisoformat(ledger['started_utc'])
    paused = 0.
    for row in ledger['pauses']:
        begin = dt.datetime.fromisoformat(row['paused_utc'])
        end = min(now,dt.datetime.fromisoformat(row['resumed_utc'])) if row.get('resumed_utc') else now
        paused += max(0., (end - begin).total_seconds())
    return max(0., (now - start).total_seconds() - paused)


def schedule(queries, block):
    generator = random.Random(280112 + block); result = []
    for repeat in [-1, 0, 1, 2]:
        ordered = list(queries); generator.shuffle(ordered)
        for q in ordered:
            methods = sorted(METHODS, key=lambda m: hashlib.sha256(f'280112:{block}:{repeat}:{q["id"]}:{m}'.encode()).digest())
            result.append(dict(index=len(result), query=q['id'], repeat=repeat, methods=methods))
    return result


def recovery_schedule(groups, cursor):
    pending = {g['query'] for g in groups[cursor:]}
    return [g for g in groups if g['repeat'] == -1 and g['query'] in pending]


def check_entry(item):
    p = Path(item['path'])
    assert p.stat().st_size == item['bytes'] and sha(p) == item['sha256'], str(p)


def verify_frozen(run=RUN):
    import campaign
    design = load(run/'design.json')
    check_entry(design['old_design'])
    campaign.guard(load(design['old_design']['path']))
    for name, digest in design['fast_code'].items(): assert sha(ROOT/name) == digest, name
    for item in design['schedules'].values(): check_entry(item)
    check_entry(design['source_manifest'])
    return design


def checkpoint(out):
    path = Path(out)/'checkpoint.json'
    if not path.exists(): return dict(next_group=0, scheduled_seconds=0., commits=[])
    result = load(path)
    assert result['next_group'] == len(result['commits'])
    return result


def validate_checkpoint(out, groups):
    cp = checkpoint(out)
    for index, item in enumerate(cp['commits']):
        check_entry(item)
        group = load(item['path'])
        assert group['kind'] == 'scheduled' and group['group'] == groups[index]
        assert len(group['records']) == 7
        assert [r['method'] for r in group['records']] == groups[index]['methods']
        assert all(r['call_order'] == index*7+i for i, r in enumerate(group['records']))
    return cp


def command(desired, text, run=RUN):
    assert desired in ['running', 'paused']
    with lock(run/'control.lock'):
        previous = load(run/'control.json')
        if previous['desired'] == desired: return previous
        row = dict(desired=desired, revision=previous['revision']+1, received_utc=utc(), user_instruction=text)
        save(run/'commands'/f'{row["revision"]:06d}.json', row)
        save(run/'control.json', row)
        return row


def wanted_pause(run=RUN):
    return load(run/'control.json')['desired'] == 'paused'


def update_status(run=RUN, **values):
    values.update(utc=utc(), profile=PROFILE)
    save(run/'status.json', values)


def main_records(item):
    out = Path(item['path'])
    if item['origin'] == 'legacy':
        with (out/'timings.jsonl').open(encoding='utf-8') as f: return [json.loads(line) for line in f]
    cp = checkpoint(out)
    return [row for commit in cp['commits'] for row in load(commit['path'])['records']]
