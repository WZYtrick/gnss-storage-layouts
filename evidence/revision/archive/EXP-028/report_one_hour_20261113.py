"""Write the completed one-hour session report after independent auditing."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json,math
ROOT=Path(__file__).resolve().parent
TZ=timezone(timedelta(hours=8))
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def local(value):return datetime.fromisoformat(value).astimezone(TZ).strftime('%H:%M:%S')
def main():
    audit=load(ROOT/'sessions/one_hour_20261113_audit.json');assert audit['all_passed']
    process_audit=load(ROOT/'sessions/one_hour_20261113_process_audit.json')
    assert process_audit['all_stopped'] and not process_audit['live_matching_processes']
    assert (ROOT/'STOP_AFTER_BLOCK').exists()
    design=load(ROOT/'campaigns/revision_v1_20261112/design.json')
    latest=load(ROOT/'sessions/construction_one_hour_20261113_v3/summary.json')
    planned={'D0m':72,'D1':30,'D2':30,'D2m':72}
    table=[]
    for ds in planned:
        done=audit['by_dataset'][ds];blocks=sum(d['dataset']==ds for d in audit['details'])
        table.append(f'| {ds} | {blocks}/6 | {done} | {planned[ds]-done} |')
    baseline_hours=6*sum(x['estimated_block_seconds'] for x in design['budget'].values())/3600
    remaining_build_seconds=0
    for ds in planned:
        done=[d for d in audit['details'] if d['dataset']==ds]
        remaining_build_seconds+=(6-len(done))*sum(d['elapsed_seconds'] for d in done)/len(done)
    total_hours=baseline_hours+remaining_build_seconds/3600
    report=f'''# EXP-028一小时测量结果与暂停状态

日期：2026年11月13日；下文时间均为北京时间。

已完成本次限时构建测量并保留现场，其他测量保持暂停，等待用户再次指示。

## 时间与完成情况

- 用户授权窗口：{local(audit['original_hour_started_utc'])}—{local(audit['deadline_utc'])}，共一小时。
- 实际停止继续启动区组：{local(audit['measurement_stopped_utc'])}。剩余时间不足以容纳下一个完整区组的预留预算，因此在完整区组之间停止。
- 共完成 **{audit['complete_blocks']}个完整区组、{audit['layout_builds']}次正式布局构建**；已完成工作进程累计运行约 **{audit['worker_active_seconds']/60:.1f}分钟**，包含准备、测量及计时外核验。
- 逐项核验并重新计算了{audit['data_files_rehashed']:,}个Parquet文件的哈希，全部与原布局对应文件一致；数据量约{audit['data_bytes_rehashed']/1024**3:.2f} GiB。

| 数据集 | 完成区组 | 完成构建 | 剩余构建 |
|---|---:|---:|---:|
{chr(10).join(table)}

## 正确性、时段变更和现场

GeoRinex全量核验此前已完成864/864项。本次完成区组的源记录数、文件数、行组数、全源输出核验及Parquet内容哈希均通过；原测量核心代码、原D2预测保持不变。

按照用户明确选择，测量从午夜后立即开始，作为本次临时时段调整记录，标签为`explicit_user_one_hour_20261113`。没有将00:00—01:00的数据标成原01:00—08:00窗口。后续统一统计需读取本次授权和来源清单。

本次发生两项接续程序问题：第一次尝试因阶段记录列表写入兼容问题未完成，已完整保留并排除；第二次接续进程因状态文件短暂占用退出，其工作进程仍完成了对应区组并写出核验凭证。随后增加计时外写入重试继续运行，截止时间未延长。上述日志及版本均保留，完成结果已另行独立核查。

`STOP_AFTER_BLOCK`继续保留。原整夜接续程序及本次限时程序均已结束，不会在下一夜自动开始其余测量。

## 剩余工作

- 标准解析器与五核心布局的配对比较：D1、D2各6区组，合计 **54,432次正式调用、18,144次预热**，尚未开始。
- 构建阶段还剩 **{audit['remaining_layout_builds']}次构建**。
- 随后进行完整统计、独立复算、摊销分析、图表与论文定稿及公开发布。

按全量解析核验和本次构建耗时估计，剩余测量约需 **{total_hours:.1f}小时有效运行时间**，其中解析基线约{baseline_hours:.1f}小时。当前是用户主动暂停状态，尚无新的恢复日期，原先按连续夜间窗口估计的完成日期不再适用。

## 核查材料

- [本次独立核查](EXP-028/sessions/one_hour_20261113_audit.json)
- [实验进程停止核查](EXP-028/sessions/one_hour_20261113_process_audit.json)
- [时段授权与来源清单](EXP-028/campaigns/revision_v1_20261112/authorized_session_manifest.json)
- [最后一次接续记录](EXP-028/sessions/construction_one_hour_20261113_v3/summary.json)
'''
    path=ROOT/'progress/EXP-028一小时测量报告_20261113.md';path.write_text(report,encoding='utf-8')
    status=dict(state='deferred_by_user',utc=datetime.now(timezone.utc).isoformat(),reason='One-hour measurement completed; remaining work postponed by user',
                completed_construction_blocks=audit['complete_blocks'],completed_layout_builds=audit['layout_builds'],
                remaining_layout_builds=audit['remaining_layout_builds'],formal_query_calls=0,warmup_query_calls=0,
                stop_marker_present=(ROOT/'STOP_AFTER_BLOCK').exists(),independent_audit=str(ROOT/'sessions/one_hour_20261113_audit.json'),report=str(path))
    (ROOT/'campaigns/revision_v1_20261112/status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(report=str(path),completed=audit['layout_builds'],remaining=audit['remaining_layout_builds'],estimated_remaining_hours=total_hours),ensure_ascii=False))
if __name__=='__main__':main()
