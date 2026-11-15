"""Stable local status/pause/resume entry for the currently activated campaign."""
from common import *
import argparse
import subprocess

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['status','pause','resume']);a=p.parse_args()
    active=load(ROOT/'active_campaign.json');script=Path(active['runner']['path'])
    assert script.resolve().parent==ROOT and sha(script)==active['runner']['sha256']
    raise SystemExit(subprocess.call([sys.executable,'-X','utf8','-B',str(script),a.command],cwd=ROOT))

if __name__=='__main__':main()
