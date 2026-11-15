"""Produce a dated progress report from the completed independent audit."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import hashlib
import json

ROOT = Path(__file__).resolve().parent
SESSION = ROOT / 'sessions/resume_until_1100_20261113'
TZ = timezone(timedelta(hours=8))


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry(path):
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path))


def local(value):
    return datetime.fromisoformat(value).astimezone(TZ)


def duration(seconds):
    seconds = round(seconds)
    return f'{seconds//3600}小时{seconds%3600//60}分{seconds%60}秒'


def main():
    audit_file = SESSION / 'post_deadline_independent_audit.json'
    audit = load(audit_file)
    assert audit['all_passed'] and audit['all_processes_stopped'] and audit['stopped_before_deadline']
    summary = load(SESSION / 'summary.json')
    now = datetime.now(timezone.utc).astimezone(TZ)
    start, stop, deadline = (local(audit[key]) for key in ['session_started_utc', 'stopped_utc', 'deadline_utc'])
    active = (stop-start).total_seconds()
    spare = (deadline-stop).total_seconds()
    layout_rows = '\n'.join(f'| {dataset} | 6/6 | {audit["layout_builds_by_dataset"][dataset]} | 0 |' for dataset in ['D0m', 'D1', 'D2', 'D2m'])
    manifest = dict(original_run='revision_v1_20261112', original_design=entry(ROOT/'campaigns/revision_v1_20261112/design.json'),
                    audit=entry(audit_file), audit_program=entry(ROOT/'audit_resume_1100_20261113.py'),
                    sessions=[dict(session=name, authorization=entry(ROOT/'sessions'/name/'authorization.json'),
                                   completed_jobs=[row['job'] for row in audit['details'] if row['session']==name])
                              for name in sorted({row['session'] for row in audit['details']})])
    manifest_path = ROOT/'campaigns/revision_v1_20261112/authorized_session_manifest_20261113_1100.json'
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    report = f'''# EXP-028本次续测进度与截止核查报告

核查时间：{now:%Y年%m月%d日 %H:%M:%S}（北京时间）。

## 停止情况

本次已于 **{stop:%Y年%m月%d日 %H:%M:%S}** 正常停止，早于用户授权的11:00截止时间。现场已保留，当前没有残留实验进程，不会自动进入下一夜测量。

- 启动：{start:%H:%M:%S}；停止：{stop:%H:%M:%S}；本次持续{duration(active)}。
- 08:29停止时，距截止还有{duration(spare)}，剩余任务全部为长解析配对区组。下一个完整区组的冻结预留预算约6小时27分，无法放入窗口，因此程序按整区组规则提前收尾。
- 主机侧检查了27个已记录的控制或工作进程身份，并扫描了本项目的运行入口，均未发现仍在执行的实验进程。
- 本次没有报错、超时中断或未完成区组；标准错误日志为空。更早一小时会话中的失败尝试仍保留并排除，不计入本次结果。

## 本次新增与累计完成

本次新增 **17个完整区组**：D1解析基线1个区组，构建16个区组、136次布局构建。

解析配对实验使用同一批216条请求，比较GeoRinex读取展开RINEX、读取压缩归档，以及W、C、C*、T、F五种列式布局；CPU容量为1，返回有序矩阵。每区组包含一次预热及三次正式调用。

| 解析配对实验 | 已完成区组 | 剩余区组 | 累计正式调用 | 累计预热 |
|---|---:|---:|---:|---:|
| D1 | 1/6 | 5 | 4,536 | 1,512 |
| D2 | 0/6 | 6 | 0 | 0 |
| 合计 | 1/12 | 11 | 4,536/54,432 | 1,512/18,144 |

全部构建测量现已完成 **204/204次**，覆盖34个数据集与布局组合、各六区组。

| 数据集 | 构建区组 | 完成构建 | 剩余构建 |
|---|---:|---:|---:|
{layout_rows}
| 合计 | 24/24 | 204 | 0 |

不能用已完成区组总数估算剩余耗时：长解析区组是当前主要工作量。

## 核验结果

本次独立核查重新读取原始日志、输入清单、各区组凭证及实际文件，没有启动新的测量。

1. 前置GeoRinex全量正确性核验保持 **864/864项通过**，核验证据和冻结代码哈希一致。
2. D1本区组的 **6,048条原始调用记录**完整、无重复，4,536次正式调用和1,512次预热均与冻结的查询及方法顺序一致。记录行数、真值摘要、进程身份、CPU/返回契约、阶段计时与授权时段均通过检查；运行时的逐次输出核验也全部通过。
3. 四个数据集的构建区组均为0—5，204次构建的源覆盖、记录数、行组数、文件数及阶段成本关系通过检查。重新读取并核验了 **{audit['parquet_files_rehashed']:,}个Parquet文件**，合计约 **{audit['parquet_bytes_rehashed']/1024**3:.2f} GiB**，全部与对应原布局文件哈希一致。
4. 测量核心代码、输入来源及原D2预测保持不变。早先一小时授权与本次截至11:00的授权分别保存，新增来源清单已覆盖三段有效会话。

当前完成的是数据完整性与正确性核查。解析基线只完成一个区组，六区组综合比较、稳定性结果及摊销结论尚未形成。

## 剩余实验与耗时

- 构建实验：已完成，无需补测。
- 解析配对实验：D1还剩5区组，D2还剩6区组，共 **49,896次正式调用、16,632次预热**。
- 本次D1完整区组实际用时约 **{duration(audit['baseline_observed_hours']*3600)}**；本次16个构建区组累计工作进程用时约 **{duration(audit['current_construction_hours']*3600)}**。
- 用D1实测耗时并按冻结的D1/D2预算比例外推，剩余测量约 **{audit['remaining_hours_observed_projection']:.1f}小时**；沿用原冻结预算约 **{audit['remaining_hours_frozen_estimate']:.1f}小时**。建议按 **58—62小时有效运行时间**理解，D2的实际单区组耗时尚待正式测量校准。
- 若恢复原 **01:00—08:00** 窗口，按每晚一个完整配对区组安排，约需 **11个夜间窗口**。当前截止授权已经结束，没有安排新的恢复日期，不能直接给出确定完成日。

测量之后还需：使统一统计与独立复算入口核验新增授权来源，完成解析基线和构建成本统计、各负载摊销点，更新图表、论文、审稿回复及复现发布包。这些工作不包含在上述净测量小时数内。

## 核查材料

- [本次独立核查](EXP-028/sessions/resume_until_1100_20261113/post_deadline_independent_audit.json)
- [主机进程停止核查](EXP-028/sessions/resume_until_1100_20261113/post_deadline_process_audit.json)
- [停止与完成清单](EXP-028/sessions/resume_until_1100_20261113/summary.json)
- [组合授权来源清单](EXP-028/campaigns/revision_v1_20261112/authorized_session_manifest_20261113_1100.json)
'''
    report_path = ROOT / 'progress/EXP-028续测进度报告_20261113_1100.md'
    report_path.write_text(report, encoding='utf-8')
    receipt = dict(state='stopped_and_audited', utc=datetime.now(timezone.utc).isoformat(), session=SESSION.name,
                   stopped_utc=audit['stopped_utc'], deadline_utc=audit['deadline_utc'], no_automatic_restart=True,
                   formal_calls=4536, warmup_calls=1512, completed_layout_builds=204,
                   remaining_baseline_blocks=11, remaining_formal_calls=49896, remaining_warmup_calls=16632,
                   estimated_measurement_hours=[audit['remaining_hours_observed_projection'],audit['remaining_hours_frozen_estimate']],
                   independent_audit=entry(audit_file), report=entry(report_path))
    (ROOT/'progress/verified_status_20261113_1100.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(report=str(report_path),remaining_hours=receipt['estimated_measurement_hours'],all_passed=True),ensure_ascii=False))


if __name__ == '__main__':
    main()
