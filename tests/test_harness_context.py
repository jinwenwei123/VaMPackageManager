"""Regression cases derived from DeepSeek Harness's retention and recovery rules."""
import io
import json
import unittest
import urllib.error
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.agent import Agent
from vampm.chat_context import bound_history, packed
from vampm.providers import AI
from vampm.server import Application
from vampm.util import Job


def tool_cycle(call_id, text):
    return [{'role':'assistant','content':None,'tool_calls':[{'id':call_id,'type':'function',
        'function':{'name':'inspect_package','arguments':'{"key":"a.garden.1"}'}}]},
        {'role':'tool','tool_call_id':call_id,'content':packed({'key':'a.garden.1','description':text})}]


class HarnessContext(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_pressure_prunes_old_tools_but_keeps_recent_evidence_verbatim(self):
        history=[{'role':'user','content':'推荐花园。'}]+tool_cycle('old','旧资料。'*50000)
        latest=tool_cycle('recent','最新核对信息。'*300)
        history+=latest
        bounded=bound_history(history)
        self.assertLess(len(packed(bounded)),len(packed(history)))
        self.assertEqual(bounded[-2:],latest)

    def test_context_overflow_recovers_once_without_reexecuting_completed_tools(self):
        app=Application(self.game,self.home)
        app.ai.save({'provider':'compatible','api_model':'fixture'})
        app.agent.data.update(provider='compatible',model='fixture',api_context_version=1,
            api_history=[{'role':'user','content':'我喜欢花园。'}]+tool_cycle('old','很长的过期资料。'*2000)
            +[{'role':'assistant','content':'之前已经核对。'}])
        app.agent._entry('user','我喜欢花园。');app.agent._entry('assistant','之前已经核对。')
        requests=[]
        def respond(request,**kwargs):
            requests.append(json.loads(request.data))
            if len(requests)==1:
                body=json.dumps({'error':{'code':'context_length_exceeded','message':'maximum context length exceeded'}}).encode()
                raise urllib.error.HTTPError(request.full_url,400,'bad request',{},io.BytesIO(body))
            answer={'role':'assistant','content':'偏好是花园。'}
            return Response(json.dumps({'choices':[{'message':answer,'finish_reason':'stop'}]}).encode())
        with mock.patch.object(app.ai.models,'reasoning',return_value=None), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond), \
             mock.patch.object(app.agent,'call') as call:
            app.agent.send('记得我的偏好吗？');app.agent.worker.join(10)
        self.assertGreaterEqual(len(requests),2)
        self.assertEqual(app.agent.data['messages'][-1]['role'],'assistant')
        self.assertIn('我喜欢花园',packed(requests[-1]['messages']))
        call.assert_not_called()

    def test_generation_accounts_summary_tags_and_joint_separately(self):
        ai=AI(self.c);ai.save({'provider':'deepseek','api_model':'deepseek-flash'})
        def respond(request,**kwargs):
            answer={'items':[{'key':'a.garden.1','category':'场景','summary':'花园场景。','tags':['花园']}]}
            return Response(json.dumps({'choices':[{'message':{'content':json.dumps(answer)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'prompt_cache_hit_tokens':64,'completion_tokens':10}}).encode())
        with mock.patch('vampm.providers.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            for target in ('summary','tags','both'):
                ai.classify_batch([{'key':'a.garden.1'}],Job(target),target)
        self.assertEqual({r['purpose'] for r in ai.usage.public()},{'summary','tags','both'})

    def long_history(self):
        return [{'role':'user' if i%2==0 else 'assistant','content':('用户偏好花园。' if i%2==0 else '已核对花园资料。')*750}
                for i in range(10)]+[{'role':'user','content':'请继续推荐，并保留之前的偏好。'}]

    def run_compaction(self,history,answer,finish='stop'):
        from vampm.agent import INSTRUCTIONS
        from vampm.request_context import ContextPolicy,RequestMeter
        app=Application(self.game,self.home)
        cfg=app.ai.settings(True);cfg.update(provider='compatible',api_model='fixture')
        app.agent.data.update(provider='compatible',model='fixture',api_history=list(history),api_context_version=1)
        tools=[{'type':'function','function':{'name':'get_status','parameters':{'type':'object'}}}]
        from vampm.language import output_instruction
        meter=RequestMeter(cfg,'fixture',output_instruction(INSTRUCTIONS,'zh-CN'),tools);requests=[]
        def respond(request,**kwargs):
            requests.append(json.loads(request.data))
            return Response(json.dumps({'choices':[{'message':answer,'finish_reason':finish}],
                'usage':{'prompt_tokens':1000,'prompt_cache_hit_tokens':900}}).encode())
        with mock.patch('urllib.request.OpenerDirector.open',side_effect=respond),mock.patch.object(app.agent,'call') as call:
            import urllib.request
            result=app.agent._manage_api_context(cfg,history,tools,meter,ContextPolicy(),urllib.request.build_opener(),'fixture')
        call.assert_not_called()
        return app,result,requests,tools

    def test_summary_replays_same_prefix_retains_tail_and_archives_source(self):
        from vampm.agent import CHECKPOINT_PREFIX,INSTRUCTIONS
        history=self.long_history()
        app,new,requests,tools=self.run_compaction(history,{'role':'assistant','content':'用户偏好花园，已核对部分资源，仍需继续推荐。'})
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]['tools'],tools)
        from vampm.language import output_instruction
        self.assertEqual(requests[0]['messages'][0],{'role':'system','content':output_instruction(INSTRUCTIONS,'zh-CN')})
        source=requests[0]['messages'][1:-1]
        self.assertEqual(source,history[:len(source)])
        self.assertEqual(requests[0]['messages'][-1]['role'],'user')
        self.assertEqual(requests[0]['tool_choice'],'auto')
        self.assertTrue(new[0]['content'].startswith(CHECKPOINT_PREFIX))
        self.assertEqual(new[-2:],history[-2:])
        archive=list((app.agent.folder/'context-checkpoints').glob('*.json'))
        self.assertEqual(len(archive),1)
        self.assertEqual(json.loads(archive[0].read_text('utf-8'))['history'],history)
        self.assertNotIn('api_history',json.loads(app.agent.path.read_text('utf-8')))
        self.assertEqual(Agent(app).data['api_history'],new)
        self.assertEqual(app.ai.usage.public()[0]['purpose'],'compaction')

    def test_failed_or_tool_call_summary_never_replaces_source(self):
        history=self.long_history()
        for answer,finish in [({'role':'assistant','content':'未完成摘要'},'length'),
                ({'role':'assistant','content':'','tool_calls':[{'id':'bad'}]},'stop')]:
            with self.assertRaisesRegex(ValueError,'未替换原文'):
                self.run_compaction(history,answer,finish)
        self.assertFalse((self.c.data/'agent/context-checkpoints').exists())

    def test_meter_prices_full_envelope_cjk_images_and_reuses_message_estimates(self):
        from vampm.request_context import RequestMeter,text_tokens
        cfg={'base_url':'https://fixture.example'}
        self.assertGreater(text_tokens('花'*1000),text_tokens('a'*1000)*3)
        tools=[{'type':'function','function':{'name':'search','description':'说明'*1000}}]
        meter=RequestMeter(cfg,'fixture','固定说明'*1000,tools)
        history=[{'role':'user','content':'花园'*1000} for _ in range(30)]
        estimated=meter.measure(history);count=meter.priced_messages
        self.assertGreater(estimated,sum(meter.price(m) for m in history))
        for _ in range(50):self.assertEqual(meter.measure(history),estimated)
        self.assertEqual(meter.priced_messages,count)
        anchor=meter.observe(history,{'prompt_tokens':estimated+2000})
        history.append({'role':'user','content':'继续'})
        self.assertGreater(meter.measure(history),estimated+2000)
        self.assertEqual(meter.priced_messages,count+1)
        other=RequestMeter(cfg,'different','固定说明'*1000,tools,anchor)
        self.assertEqual(other.measure(history),other.raw(history))
        image=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*1000000}}]}]
        self.assertLess(meter.measure(image),meter.header_tokens+10000)

    def test_pair_boundaries_reject_orphans_and_keep_parallel_calls_together(self):
        from vampm.chat_context import balanced_boundaries
        calls=[{'id':str(i),'type':'function','function':{'name':'get_status','arguments':'{}'}} for i in range(2)]
        history=[{'role':'user','content':'检查'}, {'role':'assistant','content':None,'tool_calls':calls},
            {'role':'tool','tool_call_id':'0','content':'{}'},{'role':'tool','tool_call_id':'1','content':'{}'}]
        self.assertEqual(balanced_boundaries(history),[0,1,4])
        for broken in [history[:-1],history[2:]]:
            with self.assertRaises(ValueError):balanced_boundaries(broken)

    def test_model_capacity_catalog_override_and_official_fallback_are_route_scoped(self):
        from vampm.model_catalog import model_info
        from vampm.util import atomic_json
        ai=AI(self.c);cfg=ai.settings(True)
        self.assertEqual(model_info({'id':'fixture','context_window':12345})['context_window'],12345)
        atomic_json(ai.models.file,{ai.models.source(cfg,'api'):{'models':[{'id':'fixture','context_window':32768}]}})
        self.assertEqual(ai.models.context_window(cfg,'fixture'),32768)
        self.assertEqual(ai.models.context_window(cfg,'deepseek-flash'),1048576)
        cfg['base_url']='https://proxy.example'
        self.assertEqual(ai.models.context_window(cfg,'deepseek-flash'),65536)
        cfg['api_context_window']=16384
        self.assertEqual(ai.models.context_window(cfg,'fixture'),16384)
        for invalid in [True,-1,8191,'NaN']:
            with self.assertRaises(ValueError):ai.save({'api_context_window':invalid})

    def test_generation_splits_by_input_budget_without_carrying_other_batch_outputs(self):
        ai=AI(self.c);ai.save({'provider':'deepseek','api_model':'deepseek-flash','api_context_window':32768})
        requests=[]
        def respond(request,**kwargs):
            payload=json.loads(request.data);requests.append(payload)
            items=json.loads(payload['messages'][1]['content'])
            answer={'items':[{'key':p['key'],'category':'场景','summary':'花园场景。','tags':['花园']} for p in items]}
            return Response(json.dumps({'choices':[{'message':{'content':json.dumps(answer)},'finish_reason':'stop'}]}).encode())
        items=[{'key':f'a.garden{i}.1','description':'花园。'*1700} for i in range(5)]
        with mock.patch('vampm.providers.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            result=ai.classify_batch(items,Job('budget'),'both')
        self.assertEqual(set(result),{p['key'] for p in items})
        self.assertGreater(len(requests),1)
        self.assertTrue(all(len(p['messages'])==2 for p in requests))
        self.assertTrue(all(p['messages'][0]==requests[0]['messages'][0] for p in requests))
        sent=[item['key'] for p in requests for item in json.loads(p['messages'][1]['content'])]
        self.assertEqual(sent,[p['key'] for p in items])

    def test_progress_save_does_not_rewrite_model_context_and_reset_cannot_load_old_sidecar(self):
        app=Application(self.game,self.home)
        app.agent.data.update(provider='compatible',model='fixture',api_history=self.long_history(),api_context_version=1)
        app.agent._save_api_context()
        raw=app.agent.context_path.read_bytes()
        with mock.patch('vampm.agent.atomic_json',wraps=__import__('vampm.util',fromlist=['atomic_json']).atomic_json) as write:
            app.agent._entry('assistant','界面状态更新');app.agent._save()
        self.assertEqual([call.args[0] for call in write.call_args_list],[app.agent.path])
        self.assertEqual(app.agent.context_path.read_bytes(),raw)
        app.agent.reset()
        self.assertNotIn('api_history',Agent(app).data)

    def test_legacy_context_is_migrated_before_ui_writes_drop_the_legacy_field(self):
        from vampm.util import atomic_json
        app=Application(self.game,self.home)
        legacy=dict(app.agent.data,provider='compatible',model='fixture',api_context_version=1,
                    api_history=[{'role':'user','content':'旧上下文和偏好'}])
        atomic_json(app.agent.path,legacy)
        reopened=Agent(app)
        self.assertEqual(reopened.data['api_history'],legacy['api_history'])
        self.assertEqual(json.loads(reopened.context_path.read_text('utf-8'))['history'],legacy['api_history'])
        self.assertNotIn('api_history',json.loads(reopened.path.read_text('utf-8')))

    def test_failed_context_persistence_keeps_original_in_memory(self):
        from vampm.agent import INSTRUCTIONS
        from vampm.request_context import ContextPolicy,RequestMeter
        app=Application(self.game,self.home);history=self.long_history()
        app.agent.data.update(provider='compatible',model='fixture',api_history=history,api_context_version=1)
        app.agent._save_api_context();original=app.agent.context_path.read_bytes()
        meter=RequestMeter({'base_url':'https://fixture.example'},'fixture',INSTRUCTIONS)
        from vampm.util import atomic_json
        def write(path,value):
            if path==app.agent.context_path:raise OSError('fixture disk full')
            return atomic_json(path,value)
        with mock.patch('vampm.agent.atomic_json',side_effect=write):
            with self.assertRaises(OSError):app.agent._land_context(history,[{'role':'user','content':'摘要'}],meter,ContextPolicy(),'summary')
        self.assertIs(app.agent.data['api_history'],history)
        self.assertEqual(app.agent.context_path.read_bytes(),original)

    def test_auth_or_bad_request_is_not_misclassified_as_context_overflow(self):
        from vampm.request_context import context_overflow
        for status,code in [(401,'context_length_exceeded'),(429,'rate_limit'),(400,'invalid_model')]:
            body=io.BytesIO(json.dumps({'error':{'code':code,'message':'fixture'}}).encode())
            error=urllib.error.HTTPError('https://fixture.example',status,'fixture',{},body)
            self.assertFalse(context_overflow(error));error.close()

    def test_single_package_context_error_is_not_retried_with_identical_input(self):
        ai=AI(self.c);ai.save({'provider':'deepseek','api_model':'deepseek-flash'})
        calls=[]
        def respond(request,**kwargs):
            calls.append(request)
            error=io.BytesIO(b'{"error":{"code":"context_length_exceeded"}}')
            raise urllib.error.HTTPError(request.full_url,400,'fixture',{},error)
        with mock.patch('vampm.providers.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            with self.assertRaisesRegex(ValueError,'单包资料'):
                list(ai.classification_chunks([{'key':'a.garden.1'}],Job('single')))
        self.assertEqual(len(calls),1)

    def test_switch_model_preserves_checkpoint_even_if_old_preferences_are_outside_ui_history(self):
        from vampm.agent import context_checkpoint
        app=Application(self.game,self.home)
        app.ai.save({'provider':'compatible','api_model':'new-model'})
        checkpoint=context_checkpoint('长期偏好：安静的花园，排除舞台。',[])
        app.agent.data.update(provider='compatible',model='old-model',api_history=[checkpoint],api_context_version=1)
        app.agent._entry('user','继续推荐')
        requests=[]
        def respond(request,**kwargs):
            requests.append(json.loads(request.data))
            return Response(json.dumps({'choices':[{'message':{'role':'assistant','content':'继续找花园。'}}]}).encode())
        with mock.patch.object(app.ai.models,'reasoning',return_value=None), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            app.agent.send('记得我以前的偏好吗？');app.agent.worker.join(10)
        self.assertIn('长期偏好',packed(requests[0]['messages']))
        self.assertNotIn('carry_checkpoint',app.agent.snapshot())
        self.assertNotIn('carry_checkpoint',app.agent.data)
