"""Compatibility contracts use isolated fixtures, never paid inference."""
import json
import unittest
import urllib.error
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.model_catalog import model_info
from vampm.server import Application
from vampm.util import Job


class CompatibleAPI(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_catalog_chat_tools_and_json_generation_across_endpoint_shapes(self):
        cases=[
            ('https://api.siliconflow.cn/v1','deepseek-ai/DeepSeek-V4.1-Flash','?sub_type=chat'),
            ('https://openrouter.ai/api/v1','vendor/model:free',''),
            ('https://dashscope.aliyuncs.com/compatible-mode/v1/','qwen-fixture',''),
            ('https://relay.example/custom/v1/chat/completions','private/model-v1',''),
            ('http://127.0.0.1:1234/v1','local/model',''),
        ]
        app=Application(self.game,self.home)
        self.addCleanup(app.ai.close)
        for base,model,query in cases:
            with self.subTest(base=base):
                app.ai.save({'provider':'compatible','base_url':base,'api_model':model,
                             'api_summary_model':model+'-summary'})
                cfg=app.ai.settings(True);url=app.ai.api_url(cfg)
                calls=[];chat_calls=[]
                def respond(req,**kwargs):
                    self.assertEqual(req.headers['Authorization'],'Bearer fixture-token')
                    calls.append(req)
                    if req.get_method()=='GET':
                        self.assertEqual(req.full_url,url.removesuffix('chat/completions')+'models'+query)
                        data={'data':[{'id':model},{'id':model+'-summary'}]}
                    else:
                        self.assertEqual(req.full_url,url)
                        payload=json.loads(req.data)
                        self.assertNotIn('reasoning_effort',payload)
                        if 'response_format' in payload:
                            self.assertEqual(payload['model'],model+'-summary')
                            self.assertEqual(payload['response_format'],{'type':'json_object'})
                            self.assertEqual(payload['messages'][0]['role'],'system')
                            answer={'role':'assistant','content':'{"summary":"测试简介","tags":["测试标签"]}'}
                        else:
                            self.assertEqual(payload['model'],model)
                            self.assertEqual(payload['tool_choice'],'auto');self.assertTrue(payload['tools'])
                            chat_calls.append(payload)
                            if len(chat_calls)==1:
                                answer={'role':'assistant','content':None,'tool_calls':[{'id':'check','type':'function',
                                    'function':{'name':'get_status','arguments':'{}'}}]}
                            else:
                                self.assertEqual(payload['messages'][-1]['role'],'tool')
                                self.assertEqual(payload['messages'][-1]['tool_call_id'],'check')
                                answer={'role':'assistant','content':'接口调用完成。'}
                        data={'choices':[{'message':answer,'finish_reason':'stop'}]}
                    return Response(json.dumps(data,ensure_ascii=False).encode())
                with mock.patch('vampm.model_catalog.unprotect_secret',return_value='fixture-token'), \
                     mock.patch('vampm.providers.unprotect_secret',return_value='fixture-token'), \
                     mock.patch('vampm.agent.unprotect_secret',return_value='fixture-token'), \
                     mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
                    self.assertEqual(app.ai.models.get('api')['models'][0]['id'],model)
                    app.agent.send('检查状态');app.agent.worker.join(10)
                    self.assertFalse(app.agent.busy())
                    self.assertEqual(len(chat_calls),2)
                    self.assertEqual(app.agent.snapshot()['messages'][-1]['text'],'接口调用完成。')
                    result=app.ai.complete_json(cfg,'返回 JSON 简介和标签。',[{'key':'fixture'}],
                                               {'type':'object'},Job('fixture'),cfg['api_summary_model'])
                    self.assertEqual(result,{'summary':'测试简介','tags':['测试标签']})
                self.assertEqual(len(calls),4)  # One catalog, two chat/tool turns, one JSON generation.

    def test_missing_catalog_does_not_block_custom_model_generation(self):
        app=Application(self.game,self.home)
        self.addCleanup(app.ai.close)
        app.ai.save({'provider':'compatible','base_url':'https://relay.example/v1',
                     'api_model':'private/unlisted','api_summary_model':'private/unlisted'})
        def respond(req,**kwargs):
            if req.get_method()=='GET':
                raise urllib.error.HTTPError(req.full_url,404,'missing',{},None)
            self.assertEqual(json.loads(req.data)['model'],'private/unlisted')
            return Response(b'{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}]}')
        with mock.patch('vampm.model_catalog.unprotect_secret',return_value='fixture'), \
             mock.patch('vampm.providers.unprotect_secret',return_value='fixture'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            result=app.ai.complete_json(app.ai.settings(True),'Return JSON',[],{'type':'object'},
                                       Job('fixture'),'private/unlisted')
        self.assertEqual(result,{'ok':True})

    def test_mixed_catalog_filters_declared_non_language_models_and_reads_openrouter_metadata(self):
        row=model_info({'id':'vendor/vision','architecture':{'input_modalities':['text','image'],
                        'output_modalities':['text']},'context_length':131072})
        self.assertTrue(row['vision']);self.assertEqual(row['context_window'],131072)
        self.assertIsNone(model_info({'id':'image','architecture':{'output_modalities':['image']}}))
        self.assertIsNone(model_info({'id':'embedding','sub_type':'embedding'}))
        # Generic /models only guarantees IDs: never infer capabilities or remove
        # a usable private model based on its name.
        self.assertIsNotNone(model_info({'id':'private/text-model'}))
        self.assertIsNone(model_info({'id':'vision-in-name'})['vision'])
