"""Smoke-test the built English-named EXE in an isolated, empty game directory."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

assert os.name == 'nt'
source = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory(prefix='vampm-release-language-')
root = Path(temporary.name)
home = root / 'manager'
(root / 'AddonPackages' / '中文资源目录').mkdir(parents=True)
(home / 'runtime').mkdir(parents=True)
(home / 'data').mkdir()
for file in (source / 'runtime').iterdir():
    if file.is_file():
        shutil.copy2(file, home / 'runtime' / file.name)
for folder in ('src', 'web'):
    shutil.copytree(source / folder, home / folder, ignore=shutil.ignore_patterns('__pycache__'))
exe = home / 'VaMPackageManager.exe'
shutil.copy2(source / exe.name, exe)
(home / 'data/language.json').write_text('{"language":"en"}', 'utf-8')
statefile = home / 'data/server.json'
state = None


def request(route, data=None):
    headers = {'X-Session-Token': state['token'], 'Content-Type': 'application/json'}
    req = urllib.request.Request(state['url'] + route, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read()


try:
    subprocess.run([str(exe), '--no-browser'], check=True, timeout=10)
    for _ in range(100):
        if statefile.exists():
            state = json.loads(statefile.read_text('utf-8'))
            break
        time.sleep(.1)
    assert state is not None, 'EXE did not start the service'
    status = json.loads(request('/api/status'))
    assert status['language'] == {'language': 'en'}
    assert status['version'] == '1.0.0'
    assert b'"en"' in request('/language-config.js')
    assert b'VaMPMI18n' in request('/i18n.js')
    assert b'VaMPMEnglish' in request('/locales/en.js')
    assert json.loads(request('/api/language', {'language': 'zh-CN'}))['language'] == 'zh-CN'
    assert (root / 'AddonPackages/中文资源目录').is_dir()
    print('English-named Windows EXE: startup, language assets, settings and original folder names OK')
finally:
    if state and statefile.exists():
        request('/api/shutdown', {})
    for _ in range(100):
        if not statefile.exists():
            break
        time.sleep(.1)
    assert not statefile.exists(), 'Service did not shut down cleanly'
    for attempt in range(30):
        try:
            temporary.cleanup()
            break
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(.1)
