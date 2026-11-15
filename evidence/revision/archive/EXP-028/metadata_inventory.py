"""Separate reader manifests from retained construction/intervention metadata."""
from common import *
import csv

def main():
    out=ROOT/'analysis/metadata_inventory';out.mkdir(parents=True,exist_ok=True)
    rows=[];files=[]
    for ds in ['D0m','D1','D2','D2m']:
        for method,directory in load(ROOT/'inputs'/ds/'layouts.json').items():
            directory=Path(directory);totals=dict(parquet_bytes=0,reader_metadata_bytes=0,audit_metadata_bytes=0)
            counts={k:0 for k in totals}
            for p in directory.iterdir():
                if not p.is_file():continue
                if p.suffix=='.parquet':kind='parquet_bytes'
                elif p.name in ['sources.json','catalog.json']:kind='reader_metadata_bytes'
                elif p.name in ['layout.json','boundary_plan.json']:kind='audit_metadata_bytes'
                else:raise ValueError('Unclassified stored file: '+str(p))
                totals[kind]+=p.stat().st_size;counts[kind]+=1
                if kind!='parquet_bytes':files.append(dict(dataset=ds,method=method,file=p.name,role=kind,bytes=p.stat().st_size,sha256=sha(p)))
            previous=load(ROOT/'inputs'/ds/'layout_metrics.json')[method]
            assert sum(totals.values())==previous['total_bytes'] and totals['parquet_bytes']==previous['parquet_bytes']
            rows.append(dict(dataset=ds,method=method,**totals,
                     reader_metadata_files=counts['reader_metadata_bytes'],audit_metadata_files=counts['audit_metadata_bytes'],
                     retained_total_bytes=sum(totals.values()),reader_plus_data_bytes=totals['parquet_bytes']+totals['reader_metadata_bytes']))
    for name,data in [('metadata_breakdown.csv',rows),('metadata_files.csv',files)]:
        with (out/name).open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    dump(out/'verification.json',dict(all_passed=True,configurations=len(rows),
       note='sources.json and catalog.json provide reader/source metadata; layout.json and boundary_plan.json retain construction/intervention provenance. Frozen selection costs include all retained files and are unchanged.',
       files={p.name:sha(p) for p in out.glob('*.csv')}))
    print('Classified reader and retained audit metadata for',len(rows),'layouts.')

if __name__=='__main__':main()
