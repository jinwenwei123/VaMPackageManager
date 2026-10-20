import json
import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.providers import AI, NoRedirect
from vampm.util import Cancelled, Job, atomic_json


class PromptGeneration(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_legacy_api_model_is_inherited_without_rewriting_settings(self):
        ai=AI(self.c)
        atomic_json(ai.file,{'provider':'deepseek','api_model':'my-old-model','secret':'encrypted-placeholder'})
        original=ai.file.read_bytes()
        settings=ai.settings()
        self.assertEqual(settings['api_model'],settings['api_summary_model'])
        self.assertEqual(settings['api_summary_model'],'my-old-model')
        self.assertNotIn('secret',settings);self.assertEqual(ai.file.read_bytes(),original)
        ai.save({'api_summary_model':'summary-only'})
        ai.save({'api_model':'chat-only'})
        saved=ai.settings(True)
        self.assertEqual((saved['api_model'],saved['api_summary_model']),('chat-only','summary-only'))
        self.assertEqual(saved['secret'],'encrypted-placeholder')
        before=ai.file.read_bytes()
        with self.assertRaises(ValueError):ai.save({'api_model':'changed','api_summary_model':''})
        self.assertEqual(ai.file.read_bytes(),before)

    def test_compatible_prompt_and_summary_use_separate_models_without_tools(self):
        ai=AI(self.c);ai.save({'provider':'compatible','api_model':'chat-only','api_summary_model':'summary-only'})
        requests=[]
        def open_request(opener,request,**kwargs):
            self.assertTrue(any(isinstance(h,NoRedirect) for h in opener.handlers))
            payload=json.loads(request.data);requests.append(payload)
            answer={'prompt':'从本地资源中推荐三个人物，说明风格和依赖情况。'} if payload['model']=='chat-only' else {
                'items':[{'key':'a','category':'人物','summary':'人物外观资源。'}]}
            return Response(json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}]}).encode())
        with mock.patch('vampm.providers.unprotect_secret',return_value='test-key'),mock.patch('urllib.request.OpenerDirector.open',new=open_request):
            result=ai.draft_prompt('推荐人物','只需要三个',Job('draft'))
            ai.classify_batch([{'key':'a','name':'测试人物'}],Job('summary'))
        self.assertIn('三个人物',result['preset_prompt'])
        self.assertEqual([r['model'] for r in requests],['chat-only','summary-only'])
        self.assertNotIn('tools',requests[0]);self.assertEqual(len(requests[0]['messages']),2)
        self.assertIn('只需要三个',requests[0]['messages'][1]['content'])
        self.assertFalse((self.c.data/'chat-presets.json').exists())
        self.assertFalse((self.c.data/'agent/conversation.json').exists())

    def test_codex_draft_uses_chat_model_and_discards_temporary_files(self):
        ai=AI(self.c);ai.save({'codex_model':'chat-model','codex_summary_model':'summary-model'});folders=[]
        def spawn(command,**kwargs):
            self.assertEqual(command[command.index('-m')+1],'chat-model')
            self.assertIn('--ephemeral',command);self.assertIn('--ignore-user-config',command)
            self.assertNotIn('--image',command)
            folder=Path(command[command.index('-C')+1]);folders.append(folder)
            prompt=kwargs['stdin'].read().decode('utf-8');self.assertIn('推荐服装',prompt)
            schema=json.loads((folder/'schema.json').read_text());self.assertEqual(schema['required'],['prompt'])
            Path(command[command.index('-o')+1]).write_text(json.dumps({'prompt':'推荐三套服装并说明搭配。'}),'utf-8')
            return mock.Mock(returncode=0,poll=lambda:0)
        with mock.patch.object(ai.models,'get',return_value={'models':[]}),mock.patch('vampm.providers.find_codex',return_value='codex'),mock.patch.object(ai,'spawn',side_effect=spawn):
            result=ai.draft_prompt('推荐服装','',Job('draft'))
        self.assertEqual(result['model'],'chat-model');self.assertFalse(folders[0].exists())

    def test_invalid_generated_text_and_cancel_do_not_save_or_execute(self):
        ai=AI(self.c)
        for answer in [[],{}, {'prompt':''},{'prompt':'x'*12001}]:
            with mock.patch.object(ai,'complete_json',return_value=answer):
                with self.assertRaises(ValueError):ai.draft_prompt('测试','旧提示词',Job('draft'))
        job=Job('cancelled');job.cancel.set()
        with mock.patch.object(ai,'spawn') as spawn:
            with self.assertRaises(Cancelled):ai.draft_prompt('测试','',job)
        spawn.assert_not_called();self.assertFalse((self.c.data/'chat-presets.json').exists())
