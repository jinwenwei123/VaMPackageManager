import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from vampm.agent import INSTRUCTIONS
from vampm.agent_tools import TOOLS, invoke, validate
from vampm.codex_chat import CodexChat
from vampm.server import Application
from vampm.util import Cancelled, Job


class AgentTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def test_tools_recommend_move_profile_activate_restore(self):
        original=self.put('A.Character.1');self.put('B.Other.1')
        app=Application(self.game,self.home);app.c.scan(Job('test'))
        results=invoke(app,'search_packages',{'q':'Character'})
        self.assertEqual(results['total'],1)
        key=results['items'][0]['key']
        plan=invoke(app,'plan_files',{'kind':'move','keys':[key],'folder':'精选/人物'})
        self.assertTrue(original.exists())
        invoke(app,'apply_plan',{'id':plan['id']})
        self.assertTrue((self.game/'AddonPackages/精选/人物/A.Character.1.var').exists())
        profile=invoke(app,'manage_profiles',{'action':'save','name':'人物欣赏','keys':[key]})
        self.assertEqual(next(p for p in profile if p['name']=='人物欣赏')['keys'],[key])
        plan2=invoke(app,'plan_files',{'kind':'activate','keys':[key]})
        invoke(app,'apply_plan',{'id':plan2['id']})
        self.assertEqual(app.c.stats()['active'],1)
        invoke(app,'library_task',{'action':'undo','id':plan2['id']})
        invoke(app,'library_task',{'action':'undo','id':plan['id']})
        self.assertTrue(original.exists());self.assertEqual(app.c.stats()['active'],2)

    def test_exported_tools_reject_arbitrary_commands_and_paths(self):
        self.put();app=Application(self.game,self.home);app.c.scan(Job('scan'))
        for name,args in [('shell',{}),('get_status',{'command':'evil'}),('search_packages',{'limit':10000})]:
            with self.assertRaises(ValueError):validate(name,args)
        with self.assertRaises(ValueError):
            invoke(app,'plan_files',{'kind':'move','keys':['author.scene.1'],'folder':'../outside'})
        self.assertEqual(len(app.get('/api/agent/tools',{})['tools']),len(TOOLS))

    def test_agent_read_only_blocks_mutations(self):
        self.put();app=Application(self.game,self.home);app.c.scan(Job('scan'))
        app.agent.data['read_only']=True
        self.assertEqual(app.agent.call('search_packages',{})['total'],1)
        with self.assertRaises(ValueError):app.agent.call('plan_files',{'kind':'organize','keys':['author.scene.1']})
        with self.assertRaises(ValueError):app.agent.call('manage_profiles',{'action':'save','name':'x','keys':['author.scene.1']})

    def test_summary_edit_preserves_long_text_without_relaxing_other_tool_fields(self):
        self.put();app=Application(self.game,self.home);app.c.scan(Job('scan'))
        summary=('人物外观介绍，保留可见造型与资料信息。'*150)+'完整结尾。'
        invoke(app,'label_package',{'key':'author.scene.1','summary':summary})
        self.assertEqual(app.c.detail('author.scene.1')['summary'],summary)
        with self.assertRaises(ValueError):validate('search_packages',{'q':summary})

    def test_chat_large_selection_is_complete_bounded_and_empty_selection_rejected(self):
        app=Application(self.game,self.home)
        keys=['fixture.item'+str(i)+'.1' for i in range(10000)]
        with mock.patch.object(app.agent,'_run') as run:
            app.agent.send('整理全部已选包',selected_keys=keys)
            app.agent.worker.join(timeout=10)
            prompt=run.call_args.args[0]
            self.assertIn('10000 个包',prompt)
            self.assertLess(len(prompt),2000)
        app.agent.turn_lock.release();app.agent.data['busy']=False
        with mock.patch('vampm.agent.invoke',return_value={}) as invoke_tool:
            app.agent.call('plan_files',{'kind':'organize','keys':['@selected']})
            self.assertEqual(invoke_tool.call_args.args[2]['keys'],keys)
        self.assertNotIn('selected_keys',app.agent.snapshot())
        app.agent.data['selected_keys']=[]
        with self.assertRaises(ValueError):app.agent.call('plan_files',{'kind':'organize','keys':['@selected']})

    def test_chat_worker_executes_tools_and_persists_conversation(self):
        self.put();app=Application(self.game,self.home);app.c.scan(Job('scan'))
        class FakeCodex:
            def __init__(self,*args):self.thread_id=None;self.process=None
            def connect_thread(self,*args):self.thread_id='test';return 'test'
            def turn(self,text,cancel,emit,tool):
                result=tool('search_packages',{})
                tool('manage_profiles',{'action':'save','name':'日常','keys':[result['items'][0]['key']]})
                emit('delta',{'id':'reply','text':'已创建'})
                emit('delta',{'id':'reply','text':'日常方案。'})
                emit('message',{'id':'reply','text':'已创建日常方案。'})
            def close(self):pass
        with mock.patch.object(app.ai.models,'reasoning',return_value=None),mock.patch('vampm.agent.CodexChat',FakeCodex):
            app.agent.send('创建日常方案')
            app.agent.worker.join(timeout=10)
        snapshot=app.agent.snapshot()
        self.assertFalse(snapshot['busy'])
        self.assertTrue(any(p['name']=='日常' for p in app.ops.profiles()))
        self.assertEqual(next(m['text'] for m in snapshot['messages'] if m['role']=='assistant'),'已创建日常方案。')
        self.assertFalse(app.agent.snapshot(snapshot['seq'],snapshot['id'])['messages'])
        self.assertEqual(json.loads(app.agent.path.read_text('utf-8'))['thread_id'],'test')

    def test_codex_protocol_stream_tools_resume_and_cancel(self):
        real_popen=subprocess.Popen
        def spawn(command,**kwargs):
            self.assertIn('app-server',command)
            return real_popen([sys.executable,str(Path(__file__).with_name('fake_codex.py'))],**kwargs)
        cfg={'codex_path':'','codex_model':'gpt-6.1-sol'}
        chat=CodexChat(cfg,self.home/'chat')
        cancel=threading.Event();emitted=[];calls=[]
        with mock.patch('vampm.codex_chat.find_codex',return_value='codex'),mock.patch('vampm.codex_chat.subprocess.Popen',side_effect=spawn):
            try:
                thread=chat.connect_thread(None,INSTRUCTIONS,TOOLS,cancel)
                def tool(name,args):calls.append(name);return {'stats':{'packages':42},'_images':[{'key':'fixture','url':'data:image/png;base64,fixture'}]}
                chat.turn('资源数',cancel,lambda kind,value:emitted.append((kind,value)),tool)
                chat.turn('再查一次',cancel,lambda kind,value:emitted.append((kind,value)),tool)
                self.assertEqual(calls,['get_status','get_status'])
                self.assertEqual([x[1]['text'] for x in emitted if x[0]=='message'],['当前资源数：42']*2)
                chat.close();self.assertEqual(chat.connect_thread(thread,INSTRUCTIONS,TOOLS,cancel),thread)
                timer=threading.Timer(.2,cancel.set);timer.start()
                try:
                    with self.assertRaises(Cancelled):chat.turn('wait',cancel,lambda *args:None,tool)
                finally:timer.join()
            finally:chat.close()

    def test_http_compatible_agent_uses_tools(self):
        from http.server import BaseHTTPRequestHandler,HTTPServer
        self.put();app=Application(self.game,self.home);app.c.scan(Job('scan'))
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(payload)
                if len(requests)==1:
                    answer={'role':'assistant','content':'正在检查。','tool_calls':[{'id':'call-1','type':'function','function':{'name':'get_status','arguments':'{}'}}]}
                else:
                    assert payload['messages'][-1]['role']=='tool'
                    answer={'role':'assistant','content':'已查到 1 个资源。'}
                out=json.dumps({'choices':[{'message':answer}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(out)));self.end_headers();self.wfile.write(out)
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        app.ai.save({'provider':'compatible','base_url':f'http://127.0.0.1:{server.server_port}','api_model':'fixture'})
        try:
            with mock.patch('vampm.agent.unprotect_secret',return_value='fixture-key'):
                app.agent.send('看下资源数');app.agent.worker.join(timeout=10)
            self.assertEqual(len(requests),2)
            self.assertEqual(app.agent.snapshot()['messages'][-1]['text'],'已查到 1 个资源。')
            self.assertFalse(app.agent.busy())
        finally:server.shutdown();server.server_close();thread.join()
