"""Opt-in EXE lifecycle probe against a local installation, not a fixture test."""
import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--allow-local-library', action='store_true',
                    help='Allow starting the installed app and reading its local index.')
args = parser.parse_args()
if not args.allow_local_library:
    parser.error('Review this manual probe and explicitly pass --allow-local-library.')
assert os.name=='nt'
home=Path(__file__).resolve().parents[1]
statefile=home/'data/server.json'
assert not statefile.exists(), 'Close the existing manager before this smoke test.'
exe=home/'VaMPackageManager.exe'
subprocess.run([str(exe),'--no-browser'],check=True,timeout=10)
try:
    for _ in range(100):
        if statefile.exists():break
        time.sleep(.1)
    state=json.loads(statefile.read_text('utf-8'))
    def request(path,data=None):
        headers={'X-Session-Token':state['token'],'Content-Type':'application/json'}
        req=urllib.request.Request(state['url']+'/api/'+path,headers=headers,
                                   data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
    status=request('status')
    assert status['stats']['files']>0
    subprocess.run([str(exe),'--no-browser'],check=True,timeout=10)
    time.sleep(1)
    assert json.loads(statefile.read_text('utf-8'))['pid']==state['pid']
    print('Windows EXE startup, indexed library and single-instance: OK')
finally:
    if statefile.exists():request('shutdown',{})
    for _ in range(100):
        if not statefile.exists():break
        time.sleep(.1)
    assert not statefile.exists()
    print('Clean server shutdown: OK')
