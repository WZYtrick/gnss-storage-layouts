"""Portable figures from the released, independently checked result tables."""
from common import *
import argparse
import csv

def render(analysis_root,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('MPLCONFIGDIR',str(output/'matplotlib_cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager
    candidates=[f.name for f in font_manager.fontManager.ttflist]
    font='Times New Roman' if 'Times New Roman' in candidates else 'DejaVu Serif'
    plt.rcParams.update({'font.family':font,'font.size':9,'svg.fonttype':'none','pdf.fonttype':42,
                         'axes.spines.top':False,'axes.spines.right':False})
    def read(name):
        with (Path(analysis_root)/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
    rows=read('D1_thread_costs.csv');colors=['#4775A8','#D68135','#338975','#AF6B94','#727A83']
    fig,axes=plt.subplots(1,3,figsize=(7.05,2.55),sharey=True)
    for ax,contract,title in zip(axes,CONTRACTS,['Ordered matrices','Unordered matrices','Unordered Arrow']):
        for method,color in zip(CORE,colors):
            rr=[next(r for r in rows if r['method']==method and r['contract']==contract and int(r['cpu'])==cpu) for cpu in [1,4,8,16]]
            y=np.array([float(r['B_s']) for r in rr]);lo=np.array([float(r['B_s_min']) for r in rr]);hi=np.array([float(r['B_s_max']) for r in rr])
            ax.errorbar([1,4,8,16],y,yerr=[y-lo,hi-y],color=color,marker='o',markersize=3,linewidth=.9,
                        elinewidth=.55,capsize=1.8,label=method.replace('Cstar','C*'))
        ax.set_title(title,fontsize=9);ax.set_xticks([1,4,8,16]);ax.set_xlabel('Compute-pool capacity');ax.grid(axis='y',alpha=.17)
    axes[0].set_ylabel('Cumulative query time B (s)')
    fig.legend(*axes[0].get_legend_handles_labels(),loc='upper center',ncol=5,frameon=False,fontsize=8.5)
    fig.tight_layout(rect=(0,0,1,.91),w_pad=.7)
    for ext in ['png','pdf','svg']:fig.savefig(output/('D1_threads.'+ext),dpi=500,bbox_inches='tight')
    plt.close(fig)
    fig,ax=plt.subplots(figsize=(3.4,2.35))
    for method,color in zip(CORE,colors):
        rr=[next(r for r in rows if r['method']==method and r['contract']=='ordered_dense' and int(r['cpu'])==cpu) for cpu in [1,4,8,16]]
        y=np.array([float(r['B_s']) for r in rr]);lo=np.array([float(r['B_s_min']) for r in rr]);hi=np.array([float(r['B_s_max']) for r in rr])
        ax.errorbar([1,4,8,16],y,yerr=[y-lo,hi-y],color=color,marker='o',markersize=3,linewidth=.9,
                    elinewidth=.55,capsize=1.8,label=method.replace('Cstar','C*'))
    ax.set_xticks([1,4,8,16]);ax.set_xlabel('Compute-pool capacity');ax.set_ylabel('Cumulative query time B (s)')
    ax.grid(axis='y',alpha=.17);ax.legend(loc='upper right',ncol=3,fontsize=7.5,frameon=False)
    fig.tight_layout()
    for ext in ['png','pdf','svg']:fig.savefig(output/('D1_threads_main.'+ext),dpi=500,bbox_inches='tight')
    plt.close(fig)
    rows=read('rule_sensitivity.csv');rows=[r for r in rows if r['grid']=='simplex']
    fig,axes=plt.subplots(1,2,figsize=(6.5,2.35))
    for ax,field,title in zip(axes,['mean_penalty','maximum_penalty'],['Mean excess cost','Maximum excess cost']):
        ax.bar([1,3,5],[100*float(r[field]) for r in rows],width=1.25,color=[colors[0],colors[2],colors[1]])
        for x,r in zip([1,3,5],rows):
            ax.annotate(f'{100*float(r[field]):.3f}%',(x,100*float(r[field])),xytext=(0,3),textcoords='offset points',ha='center',fontsize=8)
        ax.set_xticks([1,3,5]);ax.set_xlabel('D1 near-optimal tolerance (%)');ax.set_ylabel('D2 excess cost (%)');ax.set_title(title,fontsize=9)
        ax.margins(y=.2)
    fig.tight_layout()
    for ext in ['png','pdf','svg']:fig.savefig(output/('threshold_sensitivity.'+ext),dpi=500,bbox_inches='tight')
    plt.close(fig)
    dump(output/'figure_manifest.json',dict(font=font,source_tables={name:sha(Path(analysis_root)/name) for name in ['D1_thread_costs.csv','rule_sensitivity.csv']},
          files={p.name:sha(p) for p in output.glob('*') if p.is_file()}))
    print('Rendered thread overview, main thread figure and sensitivity figure from released tables.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--analysis-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();render(a.analysis_root,a.output)
