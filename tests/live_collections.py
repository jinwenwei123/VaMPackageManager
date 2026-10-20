"""Opt-in native Codex resume + collection workflow in a disposable garden library."""
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from vampm.agent import INSTRUCTIONS
from vampm.agent_tools import TOOLS,invoke
from vampm.codex_chat import CodexChat
from vampm.server import Application
from vampm.util import Job
from test_manager import var_bytes


def ask(app,text,read_only=False):
    before=app.agent.data['seq'];app.agent.send(text,read_only=read_only)
    deadline=time.monotonic()+240;seen=set()
    while app.agent.busy():
        for row in app.agent.snapshot(before,app.agent.data['id'])['messages']:
            if row['role']=='tool' and row['id'] not in seen:
                print('Tool: '+row['name'],flush=True);seen.add(row['id'])
        if time.monotonic()>deadline:
            app.agent.stop();app.agent.worker.join(timeout=10)
            raise TimeoutError('Collection workflow timed out')
        time.sleep(.2)
    rows=app.agent.snapshot(before,app.agent.data['id'])['messages']
    errors=[r['text'] for r in rows if r['role']=='error']
    if errors:raise ValueError('; '.join(errors))
    return rows


with tempfile.TemporaryDirectory(prefix='vampm-live-collections-') as temp:
    root=Path(temp);game=root/'game';home=root/'tool';addon=game/'AddonPackages'
    for name,folder,deps in [('Sample.Garden.1','景观/花园',['Sample.Trees.1']),
                             ('Sample.Trees.1','公共依赖',[]),('Sample.Other.1','景观/备选',[])]:
        target=addon/folder/(name+'.var');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(var_bytes(name,deps=deps,description='Neutral garden environment fixture.'))
    with mock.patch('vampm.operations.require_game_closed'):
        app=Application(game,home);app.c.scan(Job('fixture'))
        cfg=app.ai.settings(True);work=app.agent.folder/'workspace'
        old=CodexChat(cfg,work)
        try:
            old_tools=[t for t in TOOLS if t['name'] not in ('show_packages','set_favorites','list_folders')]
            thread=old.connect_thread(None,'你是资源管理助手，使用工具回答。',old_tools,threading.Event())
            old.turn('调用 get_status，告诉我有多少个资源包。',threading.Event(),lambda *a:None,
                     lambda name,args:invoke(app,name,args))
        finally:old.close()
        app.agent.data.update(thread_id=thread,provider='codex',model=cfg['codex_model']);app.agent._save()
        try:
            rows=ask(app,'推荐本地 Garden 花园环境包，核对依赖，在文字后用推荐卡片展示。不要修改文件。',True)
            upgraded_thread=app.agent.data['thread_id']
            assert upgraded_thread!=thread
            assert thread in app.agent.data['previous_threads']
            cards=[r for r in rows if r['role']=='recommendations']
            assert cards and cards[-1]['keys']==['sample.garden.1']
            assert rows[-1]['role']=='recommendations'
            print('Old conversation upgraded tools and rendered recommendation cards: OK',flush=True)
            app.agent.close();app=Application(game,home)
            rows=ask(app,'把你刚才推荐的 Garden 包收藏，然后创建名为“全部收藏”的动态收藏启动方案，再从“景观/备选”整个文件夹创建名为“备选景观”的动态方案。不要启用方案，也不要启动 VaM。')
            assert app.agent.data['thread_id']==upgraded_thread
            assert app.c.favorite_keys()==['sample.garden.1']
            assert (addon/'收藏/收藏新增/景观/花园/Sample.Garden.1.var').is_file()
            profiles={p['name']:p for p in app.ops.profiles()}
            assert profiles['全部收藏']['source']['type']=='favorites'
            assert profiles['全部收藏']['included_count']==2
            assert profiles['备选景观']['source']=={'type':'folder','folder':'景观/备选'}
            assert profiles['备选景观']['keys']==['sample.other.1']
            assert app.c.stats()['active']==3
            print('Live favorite file move + complete dynamic favorites/folder profiles with dependencies: OK',flush=True)
        finally:app.agent.close()
