"""Use the unchanged numerical report after extended-source verification."""
from fast2_report import render
from pathlib import Path
import argparse

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--allow-fixture',action='store_true')
    a=p.parse_args();render(a.source_manifest,a.output,a.allow_fixture)

