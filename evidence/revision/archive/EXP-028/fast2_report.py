"""Offline figures and a Chinese numerical report after independent verification."""
from fast2_support import *
import argparse
import csv

def table(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def render(manifest_path,output,allow_fixture=False):
    deny_network();output=Path(output);manifest=load(manifest_path);audit=load(output/'independent_verification.json')
    assert audit['all_passed'] and audit['source_manifest_sha256']==sha(manifest_path)
    assert manifest['purpose']=='formal' or allow_fixture and manifest['purpose']=='fixture'
    for name,digest in audit['outputs'].items():assert sha(output/name)==digest
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    query=table(output/'query_summary.csv');build=table(output/'build_summary.csv');space=table(output/'storage_footprint.csv');amort=table(output/'amortization_summary.csv')
    labels=dict(W='W',C='C',Cstar='C*',T='T',F='F',GeoRinex_R='GeoRinex / RINEX',GeoRinex_H='GeoRinex / CRX.gz')
    colors=['#2563eb','#0d9488','#7c3aed','#d97706','#dc2626','#475569','#111827']
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),sharey=True)
    for ax,ds in zip(axes,['D1','D2']):
        for index,method in enumerate(METHODS):
            rows=[next(r for r in query if r['dataset']==ds and r['method']==method and r['scope']==f'Q{i}') for i in range(1,7)]
            x=np.arange(6)+(index-3)*.105;y=[float(r['A_ms_arithmetic']) for r in rows]
            low=[v-float(r['A_ms_min']) for v,r in zip(y,rows)];high=[float(r['A_ms_max'])-v for v,r in zip(y,rows)]
            ax.bar(x,y,width=.095,color=colors[index],label=labels[method],yerr=[low,high],error_kw=dict(lw=.6,capsize=1))
        ax.set(yscale='log',xticks=np.arange(6),xticklabels=[f'Q{i}' for i in range(1,7)],title=ds,xlabel='Query family')
        ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    axes[0].set_ylabel('Mean query cost (ms, log scale)');axes[1].legend(fontsize=8,loc='upper left')
    fig.suptitle('Two paired query blocks; whiskers show the block range')
    if manifest['purpose']=='fixture':fig.text(.5,.5,'SYNTHETIC TEST DATA',ha='center',fontsize=30,alpha=.4,rotation=20)
    fig.tight_layout();fig.savefig(output/'query_costs.png',dpi=180);fig.savefig(output/'query_costs.pdf');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(10,4.5))
    for ax,ds in zip(axes,['D1','D2']):
        for index,method in enumerate(CORE):
            b=next(r for r in build if r['dataset']==ds and r['method']==method);s=next(r for r in space if r['dataset']==ds and r['method']==method)
            x=float(s['total_MiB']);y=float(b['from_crx_seconds_mean'])
            ax.errorbar(x,y,yerr=[[y-float(b['from_crx_seconds_min'])],[float(b['from_crx_seconds_max'])-y]],fmt='o',color=colors[index],capsize=3)
            ax.annotate(labels[method],(x,y),xytext=(5,5),textcoords='offset points')
        ax.set(title=ds,xlabel='Retained storage (MiB)',ylabel='Preparation + build + initialization (s)');ax.grid(alpha=.2);ax.margins(x=.15)
    fig.suptitle('Six fresh construction blocks; source is CRX.gz')
    if manifest['purpose']=='fixture':fig.text(.5,.5,'SYNTHETIC TEST DATA',ha='center',fontsize=30,alpha=.4,rotation=20)
    fig.tight_layout();fig.savefig(output/'construction_costs.png',dpi=180);fig.savefig(output/'construction_costs.pdf');plt.close(fig)
    lines=[]
    for ds in ['D1','D2']:
        for method in METHODS:
            r=next(x for x in query if x['dataset']==ds and x['method']==method and x['scope']=='all')
            lines.append(f'| {ds} | {labels[method]} | {float(r["A_ms_arithmetic"]):.3f} | {float(r["A_ms_min"]):.3f}—{float(r["A_ms_max"]):.3f} | {float(r["B_s_arithmetic"]):.3f} |')
    costlines=[]
    for ds in ['D1','D2']:
        for method in CORE:
            s=next(x for x in space if x['dataset']==ds and x['method']==method);b=next(x for x in build if x['dataset']==ds and x['method']==method)
            costlines.append(f'| {ds} | {labels[method]} | {float(s["total_MiB"]):.2f} | {s["data_files"]} | {float(b["from_rnx_seconds_mean"]):.2f} | {float(b["from_crx_seconds_mean"]):.2f} |')
    amortlines=[]
    for ds in ['D1','D2']:
        for method in CORE:
            values=[]
            for baseline in METHODS[-2:]:
                row=next(r for r in amort if r['dataset']==ds and r['method']==method and r['baseline']==baseline and r['scenario']=='equal')
                values.append(row['break_even_queries'] or '无有限摊销点')
            amortlines.append(f'| {ds} | {labels[method]} | {values[0]} | {values[1]} |')
    prefix='【合成测试夹具，禁止用于论文】\n\n' if manifest['purpose']=='fixture' else ''
    report=prefix+f'''# EXP-028压缩版返修结果报告

本版完成D1、D2各216条请求的七路径比较，每数据集两个逻辑配对区组；原布局主实验及构建实验保留六区组。

已核验正式调用18,144次、计划预热6,048次，另记录恢复预热{audit['recovery_warmup_calls']:,}次；204次构建全部纳入。独立复算通过{audit['checks']:,}项数值检查。恢复预热和会话段不增加独立区组数量。

## 绝对查询成本

逐请求取三次正式调用的中位调用，阶段耗时取自同一次调用。下表均值先在区组内按请求计算，再对两个区组取算术平均；范围为两个区组的实际值。

| 数据 | 路径 | 每请求均值/ms | 两区组范围/ms | 216请求累计/s |
|---|---|---:|---:|---:|
{chr(10).join(lines)}

![查询成本](query_costs.png)

完整六家族、G指标、两个区组值、阶段分解及配对比值见`query_summary.csv`、`block_costs.csv`及`paired_ratios.csv`。GeoRinex_R读取展开文件；GeoRinex_H每次从CRX.gz归档读取并解压。两者包含标准解析及统一返回适配。

## 空间与构建成本

| 数据 | 布局 | 总占用/MiB | 数据文件数 | 从RINEX准备至可查询/s | 从CRX.gz准备至可查询/s |
|---|---|---:|---:|---:|---:|
{chr(10).join(costlines)}

数值为六次重新构建的算术平均，包含相应共享准备、布局编码及初始化。总占用包含数据、必需读取元数据及保留审计元数据；三项分别列于`storage_footprint.csv`。全部34个数据集与布局组合的六区组值及阶段成本见`build_summary.csv`和`build_blocks.csv`，原压缩与展开文件大小见`source_sizes.csv`。

![构建成本](construction_costs.png)

## 等权负载摊销

摊销点为六次构建平均成本，除以两个查询区组的平均每请求节省后向上取整。

| 数据 | 布局 | 相对展开文件/次查询 | 相对压缩归档/次查询 |
|---|---|---:|---:|
{chr(10).join(amortlines)}

`amortization_summary.csv`提供253种权重情景及六构建区组×两查询区组的12种组合范围；该范围描述成本敏感性。差额非正时记录无有限摊销点。

## 返修证据对应

1. 标准解析器基线补充绝对耗时和阶段成本，对应审稿意见1。
2. 空间、文件数、六次构建及摊销表对应意见2。
3. 初始调参、D0m、D1与冻结D2的数据角色继续采用既有来源和冻结证据。
4. 线程1/4/8/16与阈值敏感性继续采用已核验的六区组既有结果。
5. 本版本提供原始调用、来源清单、检查点、额外预热、暂停台账及独立复算入口；原3036情景及D2预测保持不变。

新增解析基线为两区组描述性补充，其他实验的重复次数按各自协议报告。Word、PDF、逐条回复和公开发布包在本报告之后更新；本报告不表示外部发布已完成。

## 复算材料

- `source_manifest.json`：原始证据来源，位于本分析目录的上一级。
- `verification.json`与`independent_verification.json`：完整性检查和独立数值核验。
- `request_medians.csv`：所选中位调用；全部CSV均提供哈希。
- `budget.json`、`commands/`及各区组`segments/`：实际会话与暂停恢复记录，位于本分析目录的上一级。
'''
    (output/'结果报告.md').write_text(report,encoding='utf-8')
    save(output/'delivery.json',dict(all_passed=True,purpose=manifest['purpose'],utc=utc(),
         files={p.name:entry(p) for p in output.iterdir() if p.is_file() and p.name!='delivery.json'}))
    print('FAST2 report and figures generated offline after independent verification.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--allow-fixture',action='store_true')
    a=p.parse_args();render(a.source_manifest,a.output,a.allow_fixture)
