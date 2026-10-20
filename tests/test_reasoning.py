import json
import threading
import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.codex_chat import CodexChat
from vampm.model_catalog import LEGACY_EFFORT_FIELDS, model_info
from vampm.providers import AI
from vampm.server import Application
from vampm.util import Cancelled, Job, atomic_json


def catalog(model='test-model', levels=('low','medium','high','max'), default='high'):
    return {'models':[{'id':model,'reasoning_efforts':list(levels),'default_reasoning_effort':default}]}


class Reasoning(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_official_model_fields_preserve_order_defaults_and_unknown_models(self):
        codex=model_info({'model':'c','supportedReasoningEfforts':[
            {'reasoningEffort':'low'},{'reasoningEffort':'medium'},{'reasoningEffort':'ultra'},
            {'reasoningEffort':'low'},{'reasoningEffort':'bad value'}], 'defaultReasoningEffort':'medium'},True)
        self.assertEqual(codex['reasoning_efforts'],['low','medium','ultra'])
        self.assertEqual(codex['default_reasoning_effort'],'medium')
        api=model_info({'id':'d','effort':{'supported_levels':['low','high','max'],'default_level':'high'}})
        self.assertEqual(api['reasoning_efforts'],['low','high','max'])
        self.assertEqual(api['default_reasoning_effort'],'high')
        for row in [{'id':'unknown'},{'id':'bad','effort':{'supported_levels':'low'}},
                    {'id':'none','effort':{'supported_levels':[],'default_level':'high'}}]:
            self.assertEqual(model_info(row)['reasoning_efforts'],[])
            self.assertIsNone(model_info(row)['default_reasoning_effort'])

    def test_retired_choices_are_ignored_without_rewriting_credentials(self):
        ai=AI(self.c);atomic_json(ai.file,{'api_model':'old','secret':'encrypted-placeholder',
                                         **dict.fromkeys(LEGACY_EFFORT_FIELDS,'max')})
        original=ai.file.read_bytes()
        self.assertFalse(set(LEGACY_EFFORT_FIELDS)&ai.settings().keys())
        self.assertEqual(original,ai.file.read_bytes())
        ai.save(dict.fromkeys(LEGACY_EFFORT_FIELDS,'ultra'))
        self.assertFalse(set(LEGACY_EFFORT_FIELDS)&json.loads(ai.file.read_bytes()).keys())
        self.assertEqual(ai.settings(True)['secret'],'encrypted-placeholder')
        self.assertEqual(ai.settings()['api_summary_model'],'old')

    def test_old_catalog_refreshes_for_new_capabilities_without_losing_model_names(self):
        ai=AI(self.c);cfg=ai.settings(True);key=ai.models.source(cfg,'codex')
        atomic_json(ai.models.file,{key:{'updated':9999999999,'models':[{'id':'old'}]}})
        rows=[{'model':'old','supportedReasoningEfforts':[{'reasoningEffort':'high'}], 'defaultReasoningEffort':'high'}]
        with mock.patch.object(ai.models,'codex',return_value=rows) as fetch:
            new=ai.models.get('codex');cached=ai.models.get('codex')
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(new['models'][0]['reasoning_efforts'],['high'])
        self.assertTrue(cached['cached'])

    def test_medium_ignores_legacy_choices_and_unknown_models_keep_default(self):
        ai=AI(self.c);cfg=ai.settings(True);cancel=threading.Event()
        cfg.update(dict.fromkeys(LEGACY_EFFORT_FIELDS,'ultra'))
        with mock.patch.object(ai.models,'get',return_value=catalog()):
            for provider in ('codex','deepseek','compatible'):
                cfg['provider']=provider
                self.assertEqual(ai.models.reasoning(cfg,'test-model',cancel),'medium')
                self.assertIsNone(ai.models.reasoning(cfg,'unknown',cancel))
        with mock.patch.object(ai.models,'get',return_value=catalog(levels=('low','high','max'))):
            cfg['provider']='codex';self.assertEqual(ai.models.reasoning(cfg,'test-model',cancel),'high')
            cfg['provider']='compatible';self.assertIsNone(ai.models.reasoning(cfg,'test-model',cancel))

    def test_deepseek_medium_alias_is_confined_to_official_documented_models(self):
        ai=AI(self.c);cfg=ai.settings(True);cfg['provider']='deepseek';cancel=threading.Event()
        with mock.patch.object(ai.models,'get',return_value={'models':[]}) as get:
            for model in ('deepseek-flash','deepseek-v4-pro'):
                for base in ('https://api.deepseek.com','https://api.deepseek.com/v1/chat/completions'):
                    cfg['base_url']=base
                    self.assertEqual(ai.models.reasoning(cfg,model,cancel),'medium')
            get.assert_not_called()
            self.assertIsNone(ai.models.reasoning(cfg,'other-model',cancel))
            for base in ('https://proxy.example','https://api.deepseek.com.other.example'):
                cfg['base_url']=base
                self.assertIsNone(ai.models.reasoning(cfg,'deepseek-flash',cancel))
        with mock.patch.object(ai.models,'get',side_effect=ValueError('offline')):
            self.assertIsNone(ai.models.reasoning(cfg,'test-model',cancel))
        cfg['base_url']='https://api.deepseek.com';cancel.set()
        with self.assertRaises(Cancelled):ai.models.reasoning(cfg,'deepseek-flash',cancel)

    def test_summary_and_preset_use_medium_and_unsupported_api_omits_it(self):
        ai=AI(self.c);ai.save({'provider':'compatible','api_model':'test-model','api_summary_model':'test-model',
                              'api_effort':'max','api_summary_effort':'low'})
        payloads=[]
        def respond(request,**kwargs):
            p=json.loads(request.data);payloads.append(p)
            answer={'prompt':'推荐人物。'} if isinstance(json.loads(p['messages'][1]['content']),dict) else {
                'items':[{'key':'a','category':'人物','summary':'人物外观。'}]}
            return Response(json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}]}).encode())
        with mock.patch.object(ai.models,'get',return_value=catalog()) as get, \
             mock.patch('vampm.providers.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            ai.classify_batch([{'key':'a'}],Job('summary'));ai.draft_prompt('推荐','',Job('preset'))
            get.return_value=catalog(levels=[]);ai.classify_batch([{'key':'a'}],Job('default'))
        self.assertEqual([p.get('reasoning_effort') for p in payloads],['medium','medium',None])

    def test_codex_cli_uses_medium_for_summary_and_prompt_even_when_default_is_high(self):
        ai=AI(self.c);ai.save({'codex_model':'test-model','codex_summary_model':'test-model',
                              'codex_effort':'max','codex_summary_effort':'default'})
        commands=[]
        def spawn(command,**kwargs):
            commands.append(command)
            Path(command[command.index('-o')+1]).write_text(json.dumps({'prompt':'推荐人物。','items':[
                {'key':'a','category':'人物','summary':'人物外观。'}]}),'utf-8')
            return mock.Mock(returncode=0,poll=lambda:0)
        with mock.patch.object(ai.models,'get',return_value=catalog()), \
             mock.patch('vampm.providers.find_codex',return_value='codex'),mock.patch.object(ai,'spawn',side_effect=spawn):
            ai.classify_batch([{'key':'a'}],Job('summary'));ai.draft_prompt('推荐','',Job('prompt'))
        for command in commands:self.assertIn('model_reasoning_effort="medium"',command)

    def test_codex_turn_explicitly_overrides_existing_thread_effort_with_medium(self):
        cfg={'codex_path':'','codex_model':'test-model','_chat_effort':'max'}
        chat=CodexChat(cfg,self.home/'chat');chat.thread_id='existing'
        event={'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}}
        with mock.patch.object(chat,'rpc',return_value={'turn':{'id':'turn'}}) as rpc, \
             mock.patch.object(chat,'receive',return_value=event):
            for effort in ['medium','medium']:
                chat.cfg['_chat_effort']=effort;chat.turn('消息',threading.Event(),lambda *a:None,lambda *a:None)
                self.assertEqual(rpc.call_args.args[1].get('effort'),effort)
                self.assertEqual(rpc.call_args.args[1]['threadId'],'existing')

    def test_api_chat_setting_reaches_every_tool_round(self):
        app=Application(self.game,self.home)
        app.ai.save({'provider':'compatible','api_model':'test-model','api_effort':'max','api_summary_effort':'low'})
        payloads=[]
        def respond(request,**kwargs):
            payloads.append(json.loads(request.data))
            message={'role':'assistant','content':'完成。'} if len(payloads)>1 else {
                'role':'assistant','content':'查询。','tool_calls':[{'id':'x','type':'function','function':{
                    'name':'get_status','arguments':'{}'}}]}
            return Response(json.dumps({'choices':[{'message':message}]}).encode())
        with mock.patch.object(app.ai.models,'get',return_value=catalog()), \
             mock.patch('vampm.agent.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            app.agent.send('查询');app.agent.worker.join(10)
        self.assertEqual(len(payloads),2)
        self.assertEqual([p.get('reasoning_effort') for p in payloads],['medium','medium'])
        self.assertEqual(app.agent.snapshot()['messages'][-1]['text'],'完成。')
