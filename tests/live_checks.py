"""Explicit opt-in integration checks; default unit tests never use AI/network."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vampm.catalog import Catalog
from vampm.providers import AI, Downloads, HUB, trusted_json
from vampm.util import Job

parser=argparse.ArgumentParser()
parser.add_argument('check',choices=['codex','hub'])
args=parser.parse_args()
with tempfile.TemporaryDirectory() as td:
    root=Path(td);(root/'game').mkdir()
    c=Catalog(root/'game',root/'tool')
    if args.check=='codex':
        ai=AI(c)
        print(json.dumps(ai.status(),ensure_ascii=True),flush=True)
        result=ai.classify_batch([{'key':'sample.garden.1','name':'Garden','author':'Sample',
            'description':'A garden environment with trees and a fountain.',
            'counts':{'asset':1},'samples':['Custom/Assets/Garden.assetbundle']}],Job('AI integration'))
        print(json.dumps(result,ensure_ascii=True))
    else:
        data=trusted_json(HUB,{'source':'VaM','action':'findPackages','packages':'AcidBubbles.Timeline.1'})
        packages=data.get('packages',{})
        print(json.dumps(packages,ensure_ascii=True)[:2500])
