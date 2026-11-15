"""English reviewer-requested plots directly from checked tables."""
from common import *
import csv
import os
import numpy as np
PAPER=ROOT.parents[1]/'论文初稿/20261112-CMSDA2026返修稿'
os.environ['MPLCONFIGDIR']=str(PAPER/'build/matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

def main():
    cert=load(ROOT/'validation/independent_existing_evidence.json');assert cert['all_passed']
    source=ROOT/'analysis/existing_evidence/D1_thread_costs.csv'
    assert sha(source)==cert['output_hashes'][source.name]
    with source.open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
    font_manager.fontManager.addfont('external/times.ttf')
    plt.rcParams.update({'font.family':'Times New Roman','font.size':9,'svg.fonttype':'none','pdf.fonttype':42,
                         'axes.spines.top':False,'axes.spines.right':False})
    colors=['#4775A8','#D68135','#338975','#AF6B94','#727A83']
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
    for ext in ['png','pdf','svg']:fig.savefig(PAPER/'figures'/('D1_threads.'+ext),dpi=500,bbox_inches='tight')
    plt.close(fig)
    with (ROOT/'analysis/existing_evidence/rule_sensitivity.csv').open(encoding='utf-8') as f:rs=[r for r in csv.DictReader(f) if r['grid']=='simplex']
    fig,axes=plt.subplots(1,2,figsize=(6.5,2.35))
    for ax,field,title in zip(axes,['mean_penalty','maximum_penalty'],['Mean excess cost','Maximum excess cost']):
        ax.bar([1,3,5],[100*float(r[field]) for r in rs],width=1.25,color=[colors[0],colors[2],colors[1]])
        for x,r in zip([1,3,5],rs):
            ax.annotate(f'{100*float(r[field]):.3f}%',(x,100*float(r[field])),xytext=(0,3),textcoords='offset points',ha='center',fontsize=8)
        ax.set_xticks([1,3,5]);ax.set_xlabel('D1 near-optimal tolerance (%)');ax.set_ylabel('D2 excess cost (%)');ax.set_title(title,fontsize=9)
        ax.margins(y=.2)
    fig.tight_layout()
    for ext in ['png','pdf','svg']:fig.savefig(PAPER/'figures'/('threshold_sensitivity.'+ext),dpi=500,bbox_inches='tight')
    plt.close(fig)
    dump(PAPER/'checks/revision_figure_data.json',dict(D1_threads=rows,threshold_sensitivity=rs,
         verified_inputs=cert['output_hashes'],outputs={p.name:sha(p) for pattern in ['D1_threads.*','threshold_sensitivity.*'] for p in (PAPER/'figures').glob(pattern)}))
    print('Generated D1 thread and tolerance sensitivity figures.')

if __name__=='__main__':main()
