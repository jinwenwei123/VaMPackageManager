"""Exercise real chat requests with oversized history, switches and restarts."""
import json
import unittest
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.agent import Agent
from vampm.server import Application


class ChatContext(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_large_tool_history_keeps_earlier_dialogue_and_recommendation_keys(self):
        app=Application(self.game,self.home)
        app.ai.save({'provider':'deepseek','api_model':'deepseek-flash'})
        cfg=app.ai.settings(True)
        first='我喜欢花园场景，优先推荐安静的。'
        reply='第一个是花园资源。'
        app.agent.data.update(provider='deepseek',model=cfg['api_model'],api_history=[
            {'role':'user','content':first},
            {'role':'assistant','content':None,'tool_calls':[{'id':'large','type':'function',
                'function':{'name':'search_packages','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'large','content':json.dumps({'items':[{'key':'a.garden.1',
                'summary':'花园场景。'*30000}]},ensure_ascii=False)},
            {'role':'assistant','content':reply},
            {'role':'user','content':'继续推荐'}, {'role':'assistant','content':'仍优先花园。'}])
        app.agent._entry('user',first)
        app.agent._entry('assistant',reply)
        app.agent._entry('recommendations',keys=['a.garden.1'],items=[{'key':'a.garden.1','name':'花园'}])
        app.agent._entry('user','继续推荐');app.agent._entry('assistant','仍优先花园。')
        app.agent._save()
        app.agent=Agent(app)  # The same recovery path used after restarting the EXE.
        requests=[]
        def respond(request,**kwargs):
            requests.append(json.loads(request.data))
            return Response(json.dumps({'choices':[{'message':{'role':'assistant','content':'明白。'}}]}).encode())
        with mock.patch.object(app.ai.models,'reasoning',return_value=None), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            for text in ['第一个是什么？','我之前喜欢什么？']:
                app.agent.send(text);app.agent.worker.join(10)
                self.assertFalse(app.agent.busy())
        first_request=json.dumps(requests[0]['messages'],ensure_ascii=False)
        self.assertIn(first,first_request)
        self.assertIn('a.garden.1',first_request)
        self.assertLess(len(first_request),120000)
        # Once compacted, another small turn extends the same cacheable prefix.
        self.assertEqual(requests[1]['messages'][:len(requests[0]['messages'])],requests[0]['messages'])

    def test_switch_to_codex_carries_visible_dialogue_and_ordered_cards(self):
        app=Application(self.game,self.home)
        app.agent.data.update(provider='deepseek',model='deepseek-flash')
        app.agent._entry('user','我偏好花园场景。')
        app.agent._entry('assistant','推荐这两个资源。')
        app.agent._entry('recommendations',keys=['a.garden.1','b.garden.1'],
                         items=[{'key':'a.garden.1','name':'花园甲'},{'key':'b.garden.1','name':'花园乙'}])
        prompts=[]
        class FakeCodex:
            def __init__(self,*args):self.thread_id=None;self.process=None
            def connect_thread(self,thread,*args):self.thread_id='new-thread';return self.thread_id
            def turn(self,text,cancel,emit,tool):
                prompts.append(text);emit('message',{'id':'reply','text':'第一个是花园甲。'})
            def close(self):pass
        with mock.patch.object(app.ai.models,'reasoning',return_value=None),mock.patch('vampm.agent.CodexChat',FakeCodex):
            app.agent.send('刚才推荐的第一个叫什么？');app.agent.worker.join(10)
        self.assertEqual(len(prompts),1)
        self.assertIn('我偏好花园场景',prompts[0])
        self.assertIn('a.garden.1',prompts[0])
        self.assertLess(prompts[0].index('a.garden.1'),prompts[0].index('b.garden.1'))

    def test_switch_api_model_recovers_recommendation_keys(self):
        app=Application(self.game,self.home)
        app.ai.save({'provider':'compatible','api_model':'new-model'})
        app.agent.data.update(provider='deepseek',model='old-model')
        app.agent._entry('user','推荐花园场景')
        app.agent._entry('assistant','推荐以下两个。')
        app.agent._entry('recommendations',keys=['a.garden.1','b.garden.1'],items=[])
        requests=[]
        def respond(request,**kwargs):
            requests.append(json.loads(request.data))
            return Response(json.dumps({'choices':[{'message':{'role':'assistant','content':'花园甲。'}}]}).encode())
        with mock.patch.object(app.ai.models,'reasoning',return_value=None), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            app.agent.send('刚才推荐的第一个');app.agent.worker.join(10)
        self.assertIn('a.garden.1',json.dumps(requests[0]['messages']))

    def test_native_tool_history_compacts_without_losing_user_or_call_pairs(self):
        from vampm.chat_context import bound_history, packed
        user={'role':'user','content':'我喜欢安静的花园。'}
        history=[user,{'role':'assistant','content':None,'tool_calls':[{'id':'a','type':'function',
            'function':{'name':'search_packages','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'a','content':json.dumps({'items':[
                {'key':'a.garden.1','summary':'花园场景。'*30000}]},ensure_ascii=False)},
            {'role':'assistant','content':'推荐花园。'}, {'role':'user','content':'继续'}]
        bounded=bound_history(history)
        self.assertEqual(bounded[0],user)
        self.assertEqual(bounded[1]['tool_calls'][0]['id'],bounded[2]['tool_call_id'])
        self.assertEqual(json.loads(bounded[2]['content'])['items'][0]['key'],'a.garden.1')
        self.assertLess(len(packed(bounded)),120000)
        self.assertEqual(bound_history(bounded),bounded)

    def test_http_failure_keeps_current_request_for_next_turn(self):
        import urllib.error
        app=Application(self.game,self.home)
        app.ai.save({'provider':'compatible','api_model':'fixture'})
        requests=[]
        def respond(request,**kwargs):
            requests.append(json.loads(request.data))
            if len(requests)==1:raise urllib.error.HTTPError(request.full_url,503,'unavailable',{},None)
            return Response(json.dumps({'choices':[{'message':{'role':'assistant','content':'记住了。'}}],
                'usage':{'prompt_tokens':100,'prompt_cache_hit_tokens':64,'completion_tokens':8}}).encode())
        with mock.patch.object(app.ai.models,'reasoning',return_value=None), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            app.agent.send('我偏好花园场景。');app.agent.worker.join(10)
            app.agent=Agent(app)
            app.agent.send('我之前说喜欢什么？');app.agent.worker.join(10)
        self.assertIn('我偏好花园场景',json.dumps(requests[1],ensure_ascii=False))
        self.assertEqual(app.ai.usage.public()[0]['requests'],1)
        self.assertEqual(app.agent.snapshot()['usage']['prompt_cache_hit_tokens'],64)

    def test_reset_clears_model_context_and_visible_history(self):
        app=Application(self.game,self.home)
        app.agent._entry('user','旧偏好')
        app.agent.data['api_history']=[{'role':'user','content':'旧偏好'}]
        app.agent.data['api_context_version']=1
        app.agent.reset()
        self.assertEqual(app.agent.data['messages'],[])
        self.assertNotIn('api_history',app.agent.data)

    def test_many_large_tool_results_are_bounded_with_all_recommended_keys(self):
        from vampm.chat_context import bound_history, packed
        history=[{'role':'user','content':'推荐花园场景。'}]
        for i in range(24):
            name='show_packages' if i==23 else 'search_packages'
            history.extend([{'role':'assistant','content':None,'tool_calls':[{'id':str(i),'type':'function',
                'function':{'name':name,'arguments':'{}'}}]},
                {'role':'tool','tool_call_id':str(i),'content':packed({'items':[
                    {'key':f'a.garden{j}.1','summary':'花园场景。'*200} for j in range(12)]})}])
        bounded=bound_history(history)
        self.assertLessEqual(len(packed(bounded)),120000)
        self.assertEqual(bounded[0],history[0])
        self.assertEqual(len(bounded),len(history))
        self.assertEqual([p['key'] for p in json.loads(bounded[-1]['content'])['items']],
                         [f'a.garden{j}.1' for j in range(12)])

    def test_cancelled_tool_cycle_is_saved_with_matching_results_after_restart(self):
        from vampm.util import Cancelled
        app=Application(self.game,self.home)
        app.ai.save({'provider':'compatible','api_model':'fixture'})
        calls=[{'id':str(i),'type':'function','function':{'name':'get_status','arguments':'{}'}} for i in range(2)]
        response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':calls,
            'reasoning_content':'fixture-thinking'}}]}
        def cancelled(*args):
            app.agent.cancel.set();raise Cancelled('已停止')
        with mock.patch.object(app.ai.models,'reasoning',return_value=None), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',return_value=Response(json.dumps(response).encode())), \
             mock.patch.object(app.agent,'call',side_effect=cancelled):
            app.agent.send('检查资源');app.agent.worker.join(10)
        history=Agent(app).data['api_history']
        self.assertEqual([m['tool_call_id'] for m in history if m['role']=='tool'],['0','1'])
        self.assertEqual(history[1]['reasoning_content'],'fixture-thinking')
        self.assertFalse(app.agent.busy())

    def test_large_cover_attachment_is_sent_but_never_persisted(self):
        app=Application(self.game,self.home)
        app.ai.save({'provider':'compatible','api_model':'fixture'})
        requests=[]
        image='data:image/jpeg;base64,'+'A'*300000
        def respond(request,**kwargs):
            requests.append(json.loads(request.data))
            answer=({'role':'assistant','content':None,'tool_calls':[{'id':'cover','type':'function',
                'function':{'name':'view_package_cover','arguments':'{"key":"a.garden.1"}'}}]}
                if len(requests)==1 else {'role':'assistant','content':'已查看封面。'})
            return Response(json.dumps({'choices':[{'message':answer}]}).encode())
        with mock.patch.object(app.ai.models,'reasoning',return_value=None), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond), \
             mock.patch.object(app.agent,'call',return_value={'key':'a.garden.1',
                 '_images':[{'key':'a.garden.1','url':image}]}):
            app.agent.send('看看封面');app.agent.worker.join(10)
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[1]['messages'][-1]['content'][1]['image_url']['url'],image)
        self.assertNotIn('base64',app.agent.path.read_text('utf-8'))
        self.assertNotIn('base64',app.agent.context_path.read_text('utf-8'))
