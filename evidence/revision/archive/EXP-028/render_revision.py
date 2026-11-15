"""Produce revision figures only from a complete, independently verified analysis."""
from pathlib import Path
import argparse,csv,hashlib,json,os
from datetime import datetime,timezone

CORE=['W','C','Cstar','T','F']
METHODS=CORE+['GeoRinex_R','GeoRinex_H']
COLORS=['#4775A8','#D68135','#338975','#AF6B94','#727A83']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def label(x):return {'Cstar':'C*','GeoRinex_R':'GeoRinex\nRINEX','GeoRinex_H':'GeoRinex\nCRX.gz'}.get(x,x)
def finite(value):return None if value=='' else int(value)

def render(analysis,output,synthetic=False):
    analysis=Path(analysis);output=Path(output)
    marker=any((p/'NOT_EXPERIMENTAL_DATA.txt').exists() for p in [analysis,*analysis.parents])
    assert marker==synthetic,'Synthetic fixtures require --synthetic-demo; genuine results must not have that flag.'
    primary=json.loads((analysis/'verification.json').read_text(encoding='utf-8'))
    independent=json.loads((analysis/'independent_verification.json').read_text(encoding='utf-8'))
    assert primary['all_passed'] and independent['all_passed']
    assert primary['formal_calls']==54432 and primary['warmup_calls']==18144 and primary['layout_builds']==204
    assert independent['raw_calls']==72576 and independent['median_calls']==18144 and independent['layout_builds']==204
    for filename,expected in independent['outputs'].items():
        assert sha(analysis/filename)==expected==primary['output_hashes'][filename],filename
    query=rows(analysis/'query_summary.csv');builds=rows(analysis/'build_summary.csv')
    amort=rows(analysis/'amortization_summary.csv');paired=rows(analysis/'paired_ratios.csv')
    assert len(query)==98 and len(builds)==34 and len(amort)==5180
    output.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('MPLCONFIGDIR',str(output/'matplotlib_cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager
    font='Times New Roman' if any(f.name=='Times New Roman' for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
    plt.rcParams.update({'font.family':font,'font.size':9,'pdf.fonttype':42,'svg.fonttype':'none',
                         'axes.spines.top':False,'axes.spines.right':False})
    def save(fig,name):
        if synthetic:fig.text(.5,1.035,'SYNTHETIC VALIDATION DATA — NOT EXPERIMENTAL RESULTS',ha='center',va='bottom',fontsize=9,color='red')
        for extension in ['png','pdf','svg']:fig.savefig(output/(name+'.'+extension),dpi=400,bbox_inches='tight')
        plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(7.05,3.0))
    for ax,ds in zip(axes,['D1','D2']):
        rr=[next(r for r in query if r['dataset']==ds and r['scope']=='all' and r['method']==m) for m in METHODS]
        y=np.array([float(r['A_ms_geo']) for r in rr]);lo=np.array([float(r['A_ms_min']) for r in rr]);hi=np.array([float(r['A_ms_max']) for r in rr])
        ax.bar(range(7),y,color=COLORS+['#654A3D','#A48873'],width=.66,zorder=2)
        ax.errorbar(range(7),y,yerr=[y-lo,hi-y],fmt='none',color='black',linewidth=.65,capsize=2,zorder=3)
        ax.set_yscale('log');ax.set_ylim(min(lo)*.5,max(hi)*1.8)
        ax.set_xticks(range(7),[label(m) for m in CORE]+['RINEX','CRX.gz'],fontsize=7.5);ax.set_title(ds)
        ax.set_xlabel('Layout or GeoRinex input path')
        ax.set_ylabel('Mean query cost A (ms, logarithmic scale)');ax.grid(axis='y',alpha=.16,zorder=0)
    fig.tight_layout(rect=(0,0,1,.96));save(fig,'baseline_absolute')
    phases=['decompress_seconds','parser_seconds','adapter_seconds','overhead_seconds']
    phase_colors=['#b7c8d9','#4775A8','#D68135','#727A83']
    fig,axes=plt.subplots(1,2,figsize=(7.05,2.8))
    for ax,ds in zip(axes,['D1','D2']):
        rr=[next(r for r in query if r['dataset']==ds and r['scope']=='all' and r['method']==m) for m in METHODS[-2:]]
        bottom=np.zeros(2)
        for phase,color,title in zip(phases,phase_colors,['Decompression','GeoRinex parsing','Output adaptation','Other overhead']):
            y=np.array([float(r[phase+'_sum_mean']) for r in rr])
            ax.bar([0,1],y,bottom=bottom,color=color,width=.5,label=title);bottom+=y
        ax.set_xticks([0,1],['Expanded RINEX','CRX.gz']);ax.set_title(ds)
        ax.set_ylabel('Mean cumulative time for 216 queries (s)')
    fig.legend(*axes[0].get_legend_handles_labels(),loc='upper center',ncol=4,fontsize=8,frameon=False,bbox_to_anchor=(.5,.98))
    fig.tight_layout(rect=(0,0,1,.88));save(fig,'baseline_phases')
    allmethods=CORE+['T_time','Tperm_time','Tperm_blocksv','C_2048','C_32768','C_full','W_packed']
    fig,axes=plt.subplots(2,2,figsize=(7.05,5.3))
    for ax,ds in zip(axes.flat,['D0m','D1','D2m','D2']):
        names=[m for m in allmethods if any(r['dataset']==ds and r['method']==m for r in builds)]
        rr=[next(r for r in builds if r['dataset']==ds and r['method']==m) for m in names]
        bottom=np.zeros(len(names))
        for phase,color,title in zip(['decompress_seconds','parse_seconds','build_seconds','init_seconds'],phase_colors,
                                      ['Decompression','Shared parsing/preparation','Layout encoding/metadata','Reader initialization']):
            y=np.array([float(r[phase+'_mean']) for r in rr]);ax.bar(range(len(names)),y,bottom=bottom,color=color,width=.65,label=title);bottom+=y
        lo=np.array([float(r['from_crx_seconds_min']) for r in rr]);hi=np.array([float(r['from_crx_seconds_max']) for r in rr])
        ax.errorbar(range(len(names)),bottom,yerr=[np.maximum(0,bottom-lo),np.maximum(0,hi-bottom)],fmt='none',color='black',linewidth=.55,capsize=1.5)
        ax.set_xticks(range(len(names)),[label(m) for m in names],rotation=45,ha='right',fontsize=7)
        ax.set_ylabel('Standalone preparation and build cost (s)');ax.set_title(ds)
    fig.legend(*axes.flat[0].get_legend_handles_labels(),loc='upper center',ncol=2,frameon=False,fontsize=8)
    fig.tight_layout(rect=(0,0,1,.90),h_pad=1);save(fig,'construction_costs')
    fig,axes=plt.subplots(2,2,figsize=(7.05,5.0))
    mixture_summary=[]
    for ax,(ds,baseline) in zip(axes.flat,[(d,b) for d in ['D1','D2'] for b in METHODS[-2:]]):
        scoped=[r for r in amort if r['dataset']==ds and r['baseline']==baseline and r['scenario'] not in ['Q1','Q2','Q3','Q4','Q5','Q6']]
        possible=[finite(r['break_even_queries']) for r in scoped if r['break_even_queries']]
        left,right=(min(possible)*.8,max(possible)*1.15) if possible else (1,10)
        for method,color in zip(CORE,COLORS):
            rr=[r for r in amort if r['dataset']==ds and r['method']==method and r['baseline']==baseline and r['scenario'] not in ['Q1','Q2','Q3','Q4','Q5','Q6']]
            assert len(rr)==253
            values=sorted(v for r in rr if (v:=finite(r['break_even_queries'])) is not None)
            if values:ax.step([left]+values+[right],[0]+list(np.arange(1,len(values)+1)/253)+[len(values)/253],where='post',color=color,label=label(method),linewidth=1)
            else:ax.plot([],[],color=color,label=label(method)+' (none finite)')
            equal=next(r for r in rr if r['scenario']=='equal')
            mixture_summary.append(dict(dataset=ds,method=method,baseline=baseline,equal=equal['break_even_queries'],
                                        equal_block_min=equal['block_min'],equal_block_max=equal['block_max'],
                                        mixture_min=min(values) if values else None,mixture_max=max(values) if values else None,
                                        no_finite_scenarios=253-len(values)))
        ax.set_title(ds+' — '+('expanded RINEX' if baseline.endswith('_R') else 'CRX.gz'))
        ax.set_xscale('log');ax.set_xlim(left,right);ax.set_xlabel('Break-even request count (logarithmic scale)')
        ax.set_ylabel('Fraction of all 253 workloads');ax.set_ylim(0,1.05);ax.grid(alpha=.15)
    fig.legend(*axes.flat[0].get_legend_handles_labels(),loc='upper center',ncol=5,frameon=False,fontsize=8)
    fig.tight_layout(rect=(0,0,1,.92));save(fig,'amortization_workloads')
    report=['# Revision measurements: verified results',
            'SYNTHETIC FIXTURE; NOT EXPERIMENTAL EVIDENCE.' if synthetic else 'Post-review supplementary measurements; original D2 predictions and parameters remain frozen.',
            'The query tables use the median-total-time call for each request, retaining stages from that same call. A is the arithmetic mean within a block; G is the geometric mean; B is the sum. Central query costs and paired ratios are geometric centers across six blocks. Build costs and amortization inputs are arithmetic means.',
            '## Absolute query costs',
            '| Dataset | Method | A (ms) | Six-block A range (ms) | G (ms) | B (s) |',
            '|---|---|---:|---:|---:|---:|']
    for ds in ['D1','D2']:
        for method in METHODS:
            r=next(r for r in query if r['dataset']==ds and r['scope']=='all' and r['method']==method)
            report.append(f"| {ds} | {label(method).replace(chr(10),' ')} | {float(r['A_ms_geo']):.3f} | {float(r['A_ms_min']):.3f}–{float(r['A_ms_max']):.3f} | {float(r['G_ms_geo']):.3f} | {float(r['B_s_geo']):.3f} |")
    report+=['## Paired layout/direct-parser ratios','Values below one favor the columnar layout; no claim is made about other RINEX parsers.',
             '| Dataset | Layout | Direct-parser path | A ratio | Six-block range | Faster blocks |','|---|---|---|---:|---:|---:|']
    for r in paired:
        if r['scope']!='all' or r['metric']!='A_ms':continue
        report.append(f"| {r['dataset']} | {label(r['layout'])} | {r['baseline']} | {float(r['ratio']):.6f} | {float(r['minimum']):.6f}–{float(r['maximum']):.6f} | {r['faster_blocks']}/6 |")
    report+=['## Fresh construction costs',
             'Each row represents six fresh builds. Shared parsing/preparation is counted in full for standalone adoption. Timers end after file close and required metadata creation; full content verification is outside the timers.',
             '| Dataset | Layout | Decompress (s) | Shared prepare (s) | Build (s) | Initialize (s) | Total from archive (s) | Six-block total range (s) |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in builds:
        fields=['decompress_seconds','parse_seconds','build_seconds','init_seconds','from_crx_seconds']
        values=' | '.join(f"{float(r[f+'_mean']):.3f}" for f in fields)
        report.append(f"| {r['dataset']} | {label(r['method'])} | {values} | {float(r['from_crx_seconds_min']):.3f}–{float(r['from_crx_seconds_max']):.3f} |")
    report+=['## Amortization',
             'The request count is ceil(mean preparation cost / (mean direct cost − mean layout cost)). Query costs here are arithmetic. A nonpositive saving has no finite break-even count. The equal point is also one of the 253 workloads; individual families are additionally available in the CSV. The cumulative plot keeps all 253 workloads in its denominator, including nonfinite cases.',
             '| Dataset | Layout | Baseline | Equal workload | Equal six-block range | Finite range over 253 workloads | No finite count |',
             '|---|---|---|---:|---:|---:|---:|']
    for r in mixture_summary:
        equal=r['equal'] or 'No finite point'
        block_range=f"{r['equal_block_min']}–{r['equal_block_max']}" if r['equal_block_min'] else 'No finite point'
        span=f"{r['mixture_min']}–{r['mixture_max']}" if r['mixture_min'] is not None else 'No finite point'
        report.append(f"| {r['dataset']} | {label(r['method'])} | {r['baseline']} | {equal} | {block_range} | {span} | {r['no_finite_scenarios']}/253 |")
    report+=['## Figure definitions',
             '- baseline_absolute: block-geometric centers of arithmetic query cost A, with six-block minima and maxima; logarithmic y-axis.',
             '- baseline_phases: arithmetic mean cumulative phase times over six blocks; each phase belongs to the selected median-total-time call.',
             '- construction_costs: mean fresh construction phases, with minimum and maximum standalone total cost over six builds.',
             '- amortization_workloads: cumulative fraction of 253 mixtures reaching break-even by a request count; the horizontal axis is logarithmic.',
             'All full family results, phase sums, paired ratios, and individual block values remain in the accompanying CSV tables.']
    formatted=[]
    for index,line in enumerate(report):
        joined=index>0 and ((line.startswith('|') and report[index-1].startswith('|')) or
                            (line.startswith('- ') and report[index-1].startswith('- ')))
        formatted.append(('\n' if joined else '\n\n')+line)
    (output/'results_report.md').write_text(''.join(formatted).lstrip()+'\n',encoding='utf-8')
    # Keep tables adjacent to figures; exact original bytes allow direct auditing.
    for name in ['query_summary.csv','paired_ratios.csv','build_summary.csv','amortization_summary.csv']:
        (output/name).write_bytes((analysis/name).read_bytes())
    manifest=dict(all_passed=True,synthetic=synthetic,utc=datetime.now(timezone.utc).isoformat(),font=font,
                  source_verification_sha256=sha(analysis/'verification.json'),independent_verification_sha256=sha(analysis/'independent_verification.json'),
                  source_tables=independent['outputs'],outputs={p.name:sha(p) for p in output.iterdir() if p.is_file()})
    (output/'figure_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(all_passed=True,synthetic=synthetic,figures=4,output=str(output))))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--synthetic-demo',action='store_true');a=p.parse_args();render(a.analysis,a.output,a.synthetic_demo)
