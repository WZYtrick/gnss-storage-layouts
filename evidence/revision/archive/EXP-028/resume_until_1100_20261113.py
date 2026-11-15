"""Explicitly authorized continuation until 2026-11-13 11:00 Asia/Shanghai.

Only scheduling, authorization and out-of-timer provenance are supplied here.
The frozen campaign.baseline and construction.measure implementations are used
unchanged. Earlier sessions and their original authorizations are preserved.
"""
from common import *
import argparse
import ctypes
import math
import psutil
import subprocess
import time
import traceback
from datetime import datetime, timedelta, timezone

import campaign

RUN = 'revision_v1_20261112'
SESSION = 'resume_until_1100_20261113'
POLICY = 'explicit_user_until_1100_20261113'
SCRIPT = ROOT / 'resume_until_1100_20261113.py'
FOLDER = ROOT / 'sessions' / SESSION
CAMPAIGN = ROOT / 'campaigns' / RUN
DEADLINE = datetime(2026, 11, 13, 11, tzinfo=timezone(timedelta(hours=8)))
RESERVE = 30.0
BASE_DUMP = dump
BASE_JSON = json


def durable_dump(path, value):
    for attempt in range(100):
        try:
            BASE_DUMP(path, value)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(.1)


def identity():
    process = psutil.Process()
    return dict(pid=process.pid, created=process.create_time(), executable=process.exe())


def authorized(auth, now=None):
    now = now or datetime.now(timezone.utc)
    return datetime.fromisoformat(auth['started_utc']) <= now < datetime.fromisoformat(auth['deadline_utc'])


def remaining(auth):
    return max(0., (datetime.fromisoformat(auth['deadline_utc']) - datetime.now(timezone.utc)).total_seconds())


def next_fitting(jobs, seconds):
    return next((job for job in jobs if job['scheduling_seconds'] + RESERVE <= seconds), None)


def process_matches(item):
    try:
        return abs(psutil.Process(item['pid']).create_time() - item['created']) < .001
    except psutil.NoSuchProcess:
        return False


def check(auth):
    assert sha(CAMPAIGN / 'design.json') == auth['original_design_sha256']
    assert sha(SCRIPT) == auth['wrapper_sha256']
    original = load(CAMPAIGN / 'design.json')
    campaign.guard(original)
    assert (ROOT / 'STOP_AFTER_BLOCK').exists(), 'Legacy supervisor stop marker must remain present'
    return original


def old_completion(job):
    out = CAMPAIGN / 'runs' / job['id']
    if not out.exists():
        return None
    assert not (out / 'failure.json').exists(), f'Failed block retained: {out}'
    assert (out / 'verification.json').exists(), f'Incomplete block retained: {out}'
    certificate = load(out / 'verification.json')
    execution = load(out / 'session_execution.json')
    assert certificate['all_passed'] and execution['all_passed']
    assert certificate['execution_session'] == execution['session']
    assert execution['job'] == job and certificate['process'] == execution['process']
    for item in [certificate['authorization'], certificate['session_wrapper']]:
        assert Path(item['path']).stat().st_size == item['bytes'] and sha(item['path']) == item['sha256']
    return dict(job=job['id'], kind=job['kind'], dataset=job['dataset'], block=job['block'],
                session=execution['session'], verification=entry(out / 'verification.json'),
                formal_calls=certificate.get('formal_calls', 0),
                warmup_calls=certificate.get('warmup_calls', 0), layouts=len(certificate.get('results', [])))


class RecordJSON:
    """Apply actual authorization to raw call records after all timing ends."""
    def __init__(self, auth_entry, wrapper_entry):
        self.auth_entry = auth_entry
        self.wrapper_entry = wrapper_entry

    def dumps(self, value, *args, **kwargs):
        if isinstance(value, dict) and 'call_order' in value and 'warmup' in value:
            value = dict(value, time_policy=POLICY, execution_session=SESSION,
                         authorization_sha256=self.auth_entry['sha256'],
                         session_wrapper_sha256=self.wrapper_entry['sha256'])
        return BASE_JSON.dumps(value, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(BASE_JSON, name)


def audit_calls(out, auth, job, process):
    _, queries, truth, _ = inputs(job['dataset'])
    expected = {(q['id'], method, repeat) for q in queries for method in METHODS for repeat in [-1, 0, 1, 2]}
    seen = set()
    formal = warmup = 0
    first = datetime.fromisoformat(auth['started_utc'])
    last = datetime.fromisoformat(auth['deadline_utc'])
    previous_end = None
    with (out / 'timings.jsonl').open(encoding='utf-8') as stream:
        for order, line in enumerate(stream):
            row = BASE_JSON.loads(line)
            key = row['query'], row['method'], row['repeat']
            assert key in expected and key not in seen
            seen.add(key)
            assert row['call_order'] == order and row['process'] == process
            assert row['time_policy'] == POLICY and row['execution_session'] == SESSION
            assert row['authorization_sha256'] == sha(FOLDER / 'authorization.json')
            assert row['session_wrapper_sha256'] == auth['wrapper_sha256']
            assert row['cpu'] == 1 and row['io'] == 4 and row['contract'] == 'ordered_dense'
            assert row['dataset'] == job['dataset'] and row['block'] == job['block']
            assert row['rows'] == truth[row['query']]['rows'] and row['sha256'] == truth[row['query']]['sha256']
            begin, end = (datetime.fromisoformat(row[name]) for name in ['started_utc', 'ended_utc'])
            assert first <= begin <= end < last
            assert previous_end is None or previous_end <= begin
            previous_end = end
            assert row['warmup'] == (row['repeat'] < 0)
            warmup += row['warmup']
            formal += not row['warmup']
    assert seen == expected and formal == 4536 and warmup == 1512
    return dict(all_passed=True, formal_calls=formal, warmup_calls=warmup,
                timings=entry(out / 'timings.jsonl'), unique_call_keys=len(seen))


def worker(job_id):
    auth = load(FOLDER / 'authorization.json')
    original = check(auth)
    assert authorized(auth)
    job = next(job for job in auth['pending_jobs'] if job['id'] == job_id)
    out = CAMPAIGN / 'runs' / job_id
    assert not out.exists(), out
    process, started = identity(), utc()
    auth_entry, wrapper_entry = entry(FOLDER / 'authorization.json'), entry(SCRIPT)

    def annotated_dump(path, value):
        if isinstance(value, dict):
            value = dict(value)
            if Path(path).name == 'verification.json':
                assert authorized(auth), 'Completed outside the explicitly authorized window'
                value.update(time_policy=POLICY, execution_session=SESSION, authorization=auth_entry,
                             session_wrapper=wrapper_entry, started_utc=started, process=process,
                             dataset=job['dataset'], block=job['block'], purpose='formal')
            elif Path(path).name in ['progress.json', 'status.json', 'environment.json']:
                value.update(execution_session=SESSION, time_policy=POLICY)
        durable_dump(path, value)

    try:
        campaign.verify_assets(job['dataset'])
        if job['kind'] == 'baseline':
            campaign.in_window = lambda: authorized(auth)
            campaign.dump = annotated_dump
            campaign.json = RecordJSON(auth_entry, wrapper_entry)
            campaign.baseline(original, job, out)
            call_audit = audit_calls(out, auth, job, process)
            durable_dump(out / 'session_call_audit.json', call_audit)
        else:
            import construction
            construction.in_window = lambda: authorized(auth)
            construction.dump = annotated_dump
            construction.measure(job['dataset'], job['block'], out, smoke=False)
        check(auth)
        campaign.verify_assets(job['dataset'])
        assert authorized(auth)
        durable_dump(out / 'session_execution.json', dict(all_passed=True, session=SESSION, job=job,
                     started_utc=started, ended_utc=utc(), process=process, time_policy=POLICY,
                     authorization=auth_entry, session_wrapper=wrapper_entry))
    except BaseException:
        durable_dump(out / 'failure.json', dict(utc=utc(), session=SESSION, traceback=traceback.format_exc()))
        raise


def stop_tree(child):
    """Stop only this controller's active measurement tree on timeout/failure."""
    if child is None or child.poll() is not None:
        return
    try:
        parent = psutil.Process(child.pid)
        processes = parent.children(recursive=True) + [parent]
        for process in processes:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(processes, timeout=5)
    except psutil.NoSuchProcess:
        pass
    child.wait(timeout=10)


def completion_snapshot():
    original = load(CAMPAIGN / 'design.json')
    records = [record for job in original['jobs'] if (record := old_completion(job)) is not None]
    return dict(completed_jobs=records, completed_blocks=len(records),
                formal_query_calls=sum(row['formal_calls'] for row in records),
                warmup_query_calls=sum(row['warmup_calls'] for row in records),
                layout_builds=sum(row['layouts'] for row in records))


def run():
    FOLDER.mkdir(parents=True, exist_ok=False)
    original = load(CAMPAIGN / 'design.json')
    campaign.guard(original)
    previous_audit = load(ROOT / 'sessions/one_hour_20261113_audit.json')
    assert previous_audit['all_passed'] and previous_audit['layout_builds'] == 68
    for name in ['construction_one_hour_20261113', 'construction_one_hour_20261113_v2', 'construction_one_hour_20261113_v3']:
        old = ROOT / 'sessions' / name
        assert (old / 'finished.json').exists()
        assert not process_matches(load(old / 'authorization.json')['process'])
    legacy = load(ROOT / 'campaigns' / ('supervisor_' + RUN + '.json'))
    assert legacy.get('finished_utc') and not process_matches(legacy)
    battery = psutil.sensors_battery()
    assert battery is None or battery.power_plugged, 'AC power required'
    completed_before = [record for job in original['jobs'] if (record := old_completion(job)) is not None]
    assert len(completed_before) == 8 and sum(row['layouts'] for row in completed_before) == 68
    pending = [job for job in original['jobs'] if job['id'] not in {row['job'] for row in completed_before}]
    assert all(not (CAMPAIGN / 'runs' / job['id']).exists() for job in pending)
    start = datetime.now(timezone.utc)
    assert start < DEADLINE
    process = identity()
    auth = dict(session=SESSION, original_run=RUN, started_utc=start.isoformat(), deadline_utc=DEADLINE.isoformat(),
                time_policy=POLICY, process=process, original_design_sha256=sha(CAMPAIGN / 'design.json'),
                wrapper_sha256=sha(SCRIPT), unchanged_runtime_code=original['runtime_code'],
                user_instruction='现在继续完成后续实验直到北京时间早上11点',
                supersedes='The prior defer instruction is revoked only for this session, ending at 2026-11-13 11:00 Asia/Shanghai.',
                scheduling_rule='Take the earliest pending frozen job whose reserved whole-block budget plus 30 seconds fits. Record every bypass. No change to within-block request/method order. Do not wait for another day.',
                hard_timeout_seconds=original['baseline_hard_timeout_seconds'],
                legacy_stop_marker=entry(ROOT / 'STOP_AFTER_BLOCK'),
                independent_previous_audit=entry(ROOT / 'sessions/one_hour_20261113_audit.json'),
                completed_before=completed_before, pending_jobs=pending,
                analysis_requirement='Validate explicit session authorizations; do not relabel these calls Beijing_01_08.')
    durable_dump(FOLDER / 'authorization.json', auth)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    active = None
    active_job = None
    completed = []
    decisions = []
    reason = None
    try:
        while pending:
            if (FOLDER / 'STOP_AFTER_BLOCK').exists() or sha(ROOT / 'STOP_AFTER_BLOCK') != auth['legacy_stop_marker']['sha256']:
                reason = 'New stop request at a complete block boundary'
                break
            battery = psutil.sensors_battery()
            if battery is not None and not battery.power_plugged:
                reason = 'AC power unavailable at a complete block boundary'
                break
            job = next_fitting(pending, remaining(auth))
            if job is None:
                reason = 'No remaining complete block fits before the authorized 11:00 deadline'
                break
            decision = dict(utc=utc(), seconds_remaining=remaining(auth), selected=job['id'],
                            bypassed=[row['id'] for row in pending[:pending.index(job)]])
            decisions.append(decision)
            durable_dump(FOLDER / 'scheduling_decisions.json', decisions)
            check(auth)
            active_job = job
            log = FOLDER / (job['id'] + '.log')
            at = time.monotonic()
            with log.open('x', encoding='utf-8') as output:
                active = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', str(SCRIPT), 'worker', '--job', job['id']],
                                          cwd=str(ROOT), env={**os.environ, **ENV_THREADS}, stdout=output, stderr=subprocess.STDOUT)
                last_status = 0.
                while active.poll() is None:
                    elapsed = time.monotonic() - at
                    if remaining(auth) <= 0 or elapsed >= auth['hard_timeout_seconds']:
                        reason = 'Authorized 11:00 deadline reached' if remaining(auth) <= 0 else 'Frozen seven-hour block timeout reached'
                        stop_tree(active)
                        durable_dump(CAMPAIGN / 'runs' / job['id'] / 'failure.json', dict(
                            utc=utc(), session=SESSION, reason=reason, incomplete_block_excluded=True))
                        raise TimeoutError(reason)
                    if elapsed - last_status >= 5 or last_status == 0:
                        state = dict(state='running_authorized_continuation', utc=utc(), session=SESSION, process=process,
                                     worker_pid=active.pid, job=job, elapsed_seconds=elapsed, seconds_remaining=remaining(auth),
                                     deadline_utc=auth['deadline_utc'], completed_before=completed_before, completed_here=completed,
                                     time_policy=POLICY, next_day_restart=False)
                        durable_dump(FOLDER / 'status.json', state)
                        durable_dump(CAMPAIGN / 'status.json', state)
                        last_status = elapsed
                    time.sleep(min(1., max(.01, remaining(auth))))
                assert active.returncode == 0, f'Worker failed; retained for review: {job["id"]}'
            record = old_completion(job)
            assert record and record['session'] == SESSION
            completed.append(record)
            pending.remove(job)
            active = active_job = None
        snapshot = completion_snapshot()
        summary = dict(state='stopped_at_authorized_block_boundary', utc=utc(), session=SESSION,
                       deadline_utc=auth['deadline_utc'], started_utc=auth['started_utc'], process=process,
                       completed_here=completed, stop_reason=reason or 'All planned measurements completed',
                       remaining_jobs=[job['id'] for job in pending], next_day_restart=False,
                       authorization=entry(FOLDER / 'authorization.json'), **snapshot)
        durable_dump(FOLDER / 'summary.json', summary)
        durable_dump(FOLDER / 'status.json', summary)
        durable_dump(CAMPAIGN / 'status.json', summary)
        beijing = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        report = (f'# EXP-028 本次续测停止记录\n\n停止时间：{beijing:%Y-%m-%d %H:%M:%S}（北京时间）。\n\n'
                  f'停止原因：{summary["stop_reason"]}。最晚截止时间为2026年11月13日11:00。\n\n'
                  f'本次完成{len(completed)}个完整区组。累计正式查询{snapshot["formal_query_calls"]}/54432次，'
                  f'预热{snapshot["warmup_query_calls"]}/18144次，构建{snapshot["layout_builds"]}/204次。\n\n'
                  '现场、原始日志和真实时段授权均保留。未启动的区组等待新指示，不会次日自动恢复。'
                  '全部六区组的最终统计与独立复算尚待完成。\n')
        (FOLDER / '停止记录.md').write_text(report, encoding='utf-8')
    except BaseException:
        failure = dict(utc=utc(), session=SESSION, job=active_job, traceback=traceback.format_exc(),
                       state='failed_and_stopped', completed_here=completed, next_day_restart=False)
        stop_tree(active)
        if active_job is not None:
            destination = CAMPAIGN / 'runs' / active_job['id'] / 'failure.json'
            if not destination.exists():
                durable_dump(destination, failure)
        durable_dump(FOLDER / 'failure.json', failure)
        durable_dump(FOLDER / 'status.json', failure)
        durable_dump(CAMPAIGN / 'status.json', failure)
        raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        durable_dump(FOLDER / 'finished.json', dict(utc=utc(), process=process, active_child_exited=active is None or active.poll() is not None))


def selftest():
    auth = dict(started_utc='2026-11-13T02:00:00+08:00', deadline_utc=DEADLINE.isoformat())
    assert not authorized(auth, datetime.fromisoformat('2026-11-13T01:59:59+08:00'))
    assert authorized(auth, datetime.fromisoformat('2026-11-13T10:59:59.999999+08:00'))
    assert not authorized(auth, DEADLINE)
    jobs = [dict(id='baseline', scheduling_seconds=23200), dict(id='build', scheduling_seconds=700)]
    assert next_fitting(jobs, 24000)['id'] == 'baseline'
    assert next_fitting(jobs, 730)['id'] == 'build'
    assert next_fitting(jobs, 729) is None
    proxy = RecordJSON(dict(sha256='authorization-test'), dict(sha256='wrapper-test'))
    source = dict(call_order=0, warmup=True, time_policy='Beijing_01_08', total_seconds=.3)
    converted = BASE_JSON.loads(proxy.dumps(source, allow_nan=False))
    assert source['time_policy'] == 'Beijing_01_08'
    assert converted['time_policy'] == POLICY and converted['total_seconds'] == source['total_seconds']
    assert BASE_JSON.loads(proxy.dumps([1, 2])) == [1, 2]
    original = load(CAMPAIGN / 'design.json')
    campaign.guard(original)
    previous = completion_snapshot()
    assert previous['completed_blocks'] == 8 and previous['layout_builds'] == 68 and previous['formal_query_calls'] == 0
    receipt = dict(all_passed=True, utc=utc(), checks=['deadline boundaries', 'whole-block selection', 'record provenance without timing modification',
                   'list serialization', 'frozen runtime and input hashes', '68 completed builds retained'], wrapper=entry(SCRIPT))
    durable_dump(ROOT / 'validation/resume_until_1100_20261113_selftest.json', receipt)
    print(BASE_JSON.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'worker', 'selftest'])
    parser.add_argument('--job')
    args = parser.parse_args()
    if args.command == 'run':
        run()
    elif args.command == 'worker':
        worker(args.job)
    else:
        selftest()
