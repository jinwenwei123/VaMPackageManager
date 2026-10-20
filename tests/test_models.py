import io
import json
import os
import threading
import unittest
import urllib.error
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.model_catalog import model_info
from vampm.providers import AI
from vampm.util import Cancelled, unprotect_secret


class Models(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_metadata_controls_vision_including_unknown_and_pure_text(self):
        vision=model_info({'id':'anything','input_modalities':['text','image'],'effort':{'supported_levels':['low']}})
        self.assertIs(vision['vision'],True);self.assertIn('识图',vision['tags']);self.assertIn('推理',vision['tags'])
        text=model_info({'id':'vision-in-name','input_modalities':['text']})
        self.assertIs(text['vision'],False)
        self.assertIsNone(model_info({'id':'gpt-vision-unverified'})['vision'])
        self.assertIs(model_info({'model':'legacy'},True)['vision'],True)

    def test_codex_reads_all_pages_and_hidden_models_caches_and_refreshes(self):
        ai=AI(self.c);client=mock.Mock()
        client.rpc.side_effect=[{'data':[{'model':'one','inputModalities':['text']}],'nextCursor':'next'},
                                {'data':[{'model':'hidden','hidden':True,'inputModalities':['text','image']}]}]
        with mock.patch('vampm.codex_chat.CodexChat',return_value=client):
            first=ai.models.get('codex');cached=ai.models.get('codex')
            self.assertEqual([m['id'] for m in first['models']],['one','hidden'])
            self.assertTrue(cached['cached']);self.assertEqual(client.rpc.call_count,2)
            self.assertTrue(client.rpc.call_args_list[0].args[1]['includeHidden'])
            self.assertEqual(client.rpc.call_args_list[1].args[1]['cursor'],'next')
            client.rpc.side_effect=None;client.rpc.return_value={'data':[{'model':'new'}]}
            self.assertEqual(ai.models.get('codex',True)['models'][0]['id'],'new')
        self.assertEqual(client.close.call_count,2);self.assertFalse(ai.models.clients)

    def test_api_endpoint_auth_capabilities_and_secret_free_cache(self):
        ai=AI(self.c);ai.save({'provider':'deepseek','base_url':'https://api.deepseek.com/v1/chat/completions'})
        data={'data':[{'id':'deepseek-flash','input_modalities':['text','image']},{'id':'deepseek-v4-pro','input_modalities':['text']}]}
        def open_request(req,**kwargs):
            self.assertEqual(req.full_url,'https://api.deepseek.com/v1/models')
            self.assertEqual(req.headers['Authorization'],'Bearer private-test-token')
            return Response(json.dumps(data).encode())
        with mock.patch('vampm.model_catalog.unprotect_secret',return_value='private-test-token'),mock.patch('urllib.request.OpenerDirector.open',side_effect=open_request):
            result=ai.models.get('api')
        self.assertTrue(result['models'][0]['vision']);self.assertFalse(result['models'][1]['vision'])
        cfg=ai.settings(True)
        self.assertTrue(ai.models.supports_images(cfg,threading.Event()))
        cfg['api_model']='unknown-chat';self.assertTrue(ai.models.supports_images(cfg,threading.Event()))
        cfg['api_summary_model']='deepseek-v4-pro';self.assertFalse(ai.models.supports_images(cfg,threading.Event()))
        cfg['api_summary_model']='unknown';self.assertFalse(ai.models.supports_images(cfg,threading.Event()))
        self.assertNotIn('private-test-token',ai.models.file.read_text('utf-8'))
        self.assertNotIn('secret',json.dumps(result))

    def test_refresh_failure_retains_catalog_without_leaking_error_or_key(self):
        ai=AI(self.c)
        with mock.patch.object(ai.models,'api',return_value=[{'id':'first','input_modalities':['text']}]) as fetch:
            ai.models.get('api');fetch.side_effect=ValueError('secret-do-not-print')
            fallback=ai.models.get('api',True)
        self.assertIn('warning',fallback);self.assertNotIn('secret-do-not-print',json.dumps(fallback))
        ai.save({'base_url':'https://different.example'})
        with mock.patch.object(ai.models,'api',return_value=[{'id':'other'}]) as fetch:
            self.assertEqual(ai.models.get('api')['models'][0]['id'],'other');fetch.assert_called_once()

    def test_slow_codex_catalog_does_not_block_api_discovery(self):
        ai=AI(self.c);started=threading.Event();release=threading.Event();results=[]
        def slow(*args):
            started.set();release.wait(5)
            return [{'model':'codex-fixture'}]
        with mock.patch.object(ai.models,'codex',side_effect=slow),mock.patch.object(ai.models,'api',return_value=[{'id':'api-fixture'}]):
            worker=threading.Thread(target=lambda:results.append(ai.models.get('codex')))
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                done=threading.Event()
                api_worker=threading.Thread(target=lambda:(results.append(ai.models.get('api')),done.set()))
                api_worker.start()
                self.assertTrue(done.wait(2),'API model list should not wait for Codex')
            finally:
                release.set();worker.join(5);api_worker.join(5)
        self.assertEqual(len(json.loads(ai.models.file.read_text('utf-8'))),2)

    def test_api_error_does_not_echo_response_body_and_redirects_are_disabled(self):
        ai=AI(self.c)
        def open_request(opener,request,**kwargs):
            from vampm.providers import NoRedirect
            self.assertTrue(any(isinstance(h,NoRedirect) for h in opener.handlers))
            raise urllib.error.HTTPError(request.full_url,302,'private-test-token',{},io.BytesIO(b'private-test-token'))
        with mock.patch('vampm.model_catalog.unprotect_secret',return_value='private-test-token'),mock.patch('urllib.request.OpenerDirector.open',autospec=True,side_effect=open_request):
            with self.assertRaises(ValueError) as result:ai.models.get('api')
        self.assertNotIn('private-test-token',str(result.exception))
        ai.close()
        with self.assertRaises(Cancelled):ai.models.get('codex')

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_saved_key_encrypted_roundtrip_public_redacted_and_clear(self):
        ai=AI(self.c);key='test-only-secret-not-a-real-api-key'
        public=ai.save({'api_key':key})
        self.assertTrue(public['has_key']);self.assertNotIn('secret',public);self.assertNotIn('api_key',public)
        self.assertNotIn(key,ai.file.read_text('utf-8'))
        self.assertEqual(unprotect_secret(ai.settings(True)['secret']),key)
        ai.save({'api_key':''});self.assertEqual(unprotect_secret(ai.settings(True)['secret']),key)
        self.assertFalse(ai.save({'clear_key':True})['has_key'])
