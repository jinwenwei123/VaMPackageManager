import json
import threading
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.providers import AI
from vampm.util import atomic_json


class APIProfiles(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_switch_restores_endpoint_models_context_and_separate_encrypted_keys(self):
        ai=AI(self.c)
        with mock.patch('vampm.providers.protect_secret',side_effect=lambda key:'encrypted:'+key):
            ai.save({'provider':'deepseek','api_model':'deepseek-v4-pro','api_summary_model':'deepseek-flash',
                     'api_key':'official-fixture','api_context_window':1048576})
            ai.save({'provider':'compatible','base_url':'https://api.siliconflow.cn/v1','api_model':'private/chat',
                     'api_summary_model':'private/summary','api_key':'compatible-fixture','api_context_window':131072})
        ai.save({'provider':'deepseek'})
        cfg=ai.settings(True)
        self.assertEqual(cfg['base_url'],'https://api.deepseek.com')
        self.assertEqual(cfg['api_model'],'deepseek-v4-pro')
        self.assertEqual(cfg['api_summary_model'],'deepseek-flash')
        self.assertEqual(cfg['secret'],'encrypted:official-fixture')
        self.assertEqual(cfg['api_context_window'],1048576)
        ai=AI(self.c);ai.save({'provider':'compatible'})
        cfg=ai.settings(True)
        self.assertEqual(cfg['base_url'],'https://api.siliconflow.cn/v1')
        self.assertEqual(cfg['api_model'],'private/chat')
        self.assertEqual(cfg['secret'],'encrypted:compatible-fixture')
        self.assertEqual(cfg['api_context_window'],131072)

    def test_public_profiles_never_contain_encrypted_or_plaintext_secrets(self):
        ai=AI(self.c)
        with mock.patch('vampm.providers.protect_secret',side_effect=lambda key:'encrypted:'+key):
            ai.save({'provider':'deepseek','api_key':'official-fixture'})
            ai.save({'provider':'compatible','api_key':'compatible-fixture'})
        public=ai.settings()
        self.assertTrue(public['api_profiles']['deepseek']['has_key'])
        self.assertTrue(public['api_profiles']['compatible']['has_key'])
        for text in ('encrypted:','official-fixture','compatible-fixture','"secret"','api_connections'):
            self.assertNotIn(text,json.dumps(public))
        ai.save({'provider':'compatible','clear_key':True})
        self.assertFalse(ai.settings()['has_key'])
        ai.save({'provider':'deepseek'})
        self.assertEqual(ai.settings(True)['secret'],'encrypted:official-fixture')

    def test_legacy_connection_is_preserved_without_writing_and_not_copied_to_another_service(self):
        ai=AI(self.c)
        legacy={'provider':'compatible','base_url':'https://api.siliconflow.cn/v1',
                'api_model':'private/model','secret':'encrypted-existing'}
        atomic_json(ai.file,legacy);original=ai.file.read_bytes()
        public=ai.settings()
        self.assertEqual(original,ai.file.read_bytes())
        self.assertEqual(public['api_profile'],'compatible')
        self.assertEqual(public['api_profiles']['compatible']['api_summary_model'],'private/model')
        self.assertFalse(public['api_profiles']['deepseek']['has_key'])
        ai.save({'provider':'deepseek'})
        self.assertEqual(ai.settings(True)['secret'],'')
        ai.save({'provider':'compatible'})
        self.assertEqual(ai.settings(True)['secret'],'encrypted-existing')
        self.assertEqual(ai.settings()['api_model'],'private/model')
        # A compatible service intentionally pointing at the official endpoint
        # is still its own configured profile.
        atomic_json(ai.file,dict(legacy,base_url='https://api.deepseek.com'))
        ai.save({'api_model':'custom'})
        self.assertEqual(ai.settings(True)['secret'],'encrypted-existing')
        self.assertEqual(ai.settings()['api_profile'],'compatible')

    def test_codex_switch_does_not_replace_saved_api_configuration(self):
        ai=AI(self.c)
        ai.save({'provider':'compatible','base_url':'https://relay.example/v1','api_model':'private/chat'})
        ai.save({'provider':'codex'})
        self.assertEqual(ai.settings()['api_profile'],'compatible')
        ai.save({'provider':'deepseek'})
        ai.save({'provider':'codex','api_profile':'compatible','api_summary_model':'private/summary'})
        ai.save({'provider':'compatible'})
        self.assertEqual(ai.settings()['api_model'],'private/chat')
        self.assertEqual(ai.settings()['api_summary_model'],'private/summary')

    def test_model_discovery_uses_requested_service_without_changing_active_service(self):
        ai=AI(self.c)
        with mock.patch('vampm.providers.protect_secret',side_effect=lambda key:'encrypted:'+key):
            ai.save({'provider':'deepseek','api_key':'official-fixture'})
            ai.save({'provider':'compatible','base_url':'https://relay.example/v1','api_key':'compatible-fixture'})
        original=ai.file.read_bytes()
        def discover(cfg,cancel):
            self.assertEqual(cfg['base_url'],'https://api.deepseek.com')
            self.assertEqual(cfg['secret'],'encrypted:official-fixture')
            return [{'id':'deepseek-flash'}]
        with mock.patch.object(ai.models,'api',side_effect=discover):
            self.assertEqual(ai.models.get('api',True,threading.Event(),service='deepseek')['models'][0]['id'],'deepseek-flash')
        self.assertEqual(ai.file.read_bytes(),original)
        self.assertEqual(ai.settings()['provider'],'compatible')

    def test_invalid_profile_does_not_write_partial_state(self):
        ai=AI(self.c);ai.save({'provider':'codex'});original=ai.file.read_bytes()
        with self.assertRaises(ValueError):ai.save({'api_profile':'unknown'})
        self.assertEqual(ai.file.read_bytes(),original)
