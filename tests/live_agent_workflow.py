"""Opt-in real Codex chat and file-operation test, exclusively in a disposable fixture."""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from vampm.server import Application
from vampm.util import Job
from test_manager import var_bytes


def ask(app, message, read_only=False, selection=None):
    before=app.agent.data['seq']
    app.post('/api/agent/send',{'message':message,'read_only':read_only,'selected_keys':selection or []})
    deadline=time.monotonic()+240
    shown=set()
    while app.agent.busy():
        for row in app.agent.snapshot(before,app.agent.data['id'])['messages']:
            if row['role']=='tool' and row['id'] not in shown:
                print('Tool: '+row['name'],flush=True);shown.add(row['id'])
        if time.monotonic()>deadline:
            app.agent.stop();app.agent.worker.join(timeout=10)
            raise TimeoutError('Live agent workflow exceeded 240 seconds')
        time.sleep(.2)
    rows=app.agent.snapshot(before,app.agent.data['id'])['messages']
    errors=[r['text'] for r in rows if r['role']=='error']
    if errors: raise ValueError('; '.join(errors))
    for row in rows:
        if row['role']=='assistant': print(json.dumps({'reply':row['text']},ensure_ascii=True),flush=True)
    return rows


with tempfile.TemporaryDirectory(prefix='vampm-live-agent-') as temp:
    root=Path(temp);game=root/'game';home=root/'tool'
    folder=game/'AddonPackages';folder.mkdir(parents=True)
    for name,deps in [('Sample.Garden.1',['Sample.Trees.1']),('Sample.Trees.1',[]),('Sample.Other.1',[])]:
        (folder/(name+'.var')).write_bytes(var_bytes(name,deps=deps,description='Neutral garden environment test fixture.'))
    # Only fixtures are moved; a running real VaM must not interfere with this isolated check.
    with mock.patch('vampm.operations.require_game_closed'):
        app=Application(game,home);app.c.scan(Job('fixture scan'))
        try:
            ask(app,'检索 Garden，核对它的依赖是否齐全，用两句中文说明。不要修改文件。',True)
            assert not app.ops.history()
            print('Live search + dependency recommendation: OK',flush=True)
            ask(app,'把刚才的 Garden 包移动到 测试精选/场景，创建并启用名为“真实对话测试”的方案，只选 Garden，依赖按规则包含。不要启动游戏。',
                selection=['sample.garden.1'])
            assert (folder/'测试精选/场景/Sample.Garden.1.var').is_file()
            profile=app.ops.profiles()[0]
            assert profile['name']=='真实对话测试' and profile['keys']==['sample.garden.1']
            assert app.c.stats()['active']==2
            assert app.c.packages()['sample.other.1']['active']==False
            thread=app.agent.data['thread_id']
            print('Live move + create profile + activate with dependencies: OK',flush=True)
            app.agent.close()
            app=Application(game,home)
            ask(app,'恢复你刚才执行的所有文件移动操作，按从新到旧的顺序恢复。保留启动方案。不要启动游戏。')
            assert app.agent.data['thread_id']==thread
            for name in ('Sample.Garden.1','Sample.Trees.1','Sample.Other.1'):
                assert (folder/(name+'.var')).is_file()
            assert app.c.stats()['active']==3
            print('Live resumed conversation + reverse-order undo: OK',flush=True)
        finally: app.agent.close()
