"""Verify deterministic shared prefixes and service-reported cache accounting offline."""
import json
import unittest
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.ai_usage import APIUsage
from vampm.providers import AI
from vampm.util import Job


class APICache(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_generation_has_stable_schema_prefix_and_canonical_variable_inputs(self):
        ai=AI(self.c);ai.save({'provider':'deepseek','api_model':'deepseek-flash'})
        requests=[]
        def respond(request,**kwargs):
            payload=json.loads(request.data);requests.append(payload)
            items=json.loads(payload['messages'][1]['content'])
            answer={'items':[{'key':p['key'],'category':'场景','summary':'花园场景。'} for p in items]}
            return Response(json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}],
                'usage':{'prompt_tokens':1000,'completion_tokens':40,'total_tokens':1040,
                         'prompt_cache_hit_tokens':768,'prompt_cache_miss_tokens':232}}).encode())
        with mock.patch('vampm.providers.unprotect_secret',return_value='private-key'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=respond):
            ai.classify_batch([{'key':'a.garden.1','name':'花园','counts':{'scene':2,'hair':1}}],Job('first'))
            ai.classify_batch([{'counts':{'hair':1,'scene':2},'name':'花园','key':'a.garden.1'}],Job('same'))
            ai.classify_batch([{'key':'b.garden.1','name':'另一个花园'}],Job('next'))
        self.assertEqual(requests[0]['messages'],requests[1]['messages'])
        self.assertEqual(requests[0]['messages'][0],requests[2]['messages'][0])
        self.assertNotIn('a.garden.1',requests[0]['messages'][0]['content'])
        self.assertIn('JSON schema',requests[0]['messages'][0]['content'])
        content=requests[0]['messages'][1]['content']
        self.assertIn('花园',content);self.assertNotIn(': ',content)
        usage=APIUsage(self.c.data).public()[0]
        self.assertEqual(usage['requests'],3)
        self.assertEqual(usage['prompt_cache_hit_tokens'],2304)
        self.assertEqual(usage['cache_observed_tokens'],3000)
        self.assertNotIn('private-key',(self.c.data/'ai-usage.json').read_text())
        self.assertNotIn('花园',(self.c.data/'ai-usage.json').read_text())

    def test_usage_is_weighted_separates_sources_and_ignores_unreported_cache(self):
        usage=APIUsage(self.c.data)
        cfg={'provider':'compatible','base_url':'https://fixture.example'}
        usage.record(cfg,'m','chat',{'usage':{'prompt_tokens':100,'completion_tokens':5,
            'prompt_cache_hit_tokens':90,'prompt_cache_miss_tokens':10}})
        usage.record(cfg,'m','chat',{'usage':{'prompt_tokens':900,'prompt_tokens_details':{'cached_tokens':90}}})
        usage.record(cfg,'m','chat',{'usage':{'prompt_tokens':5000}})
        row=usage.public()[0]
        self.assertEqual(row['prompt_tokens'],6000)
        self.assertEqual(row['cache_observed_tokens'],1000)
        self.assertEqual(row['prompt_cache_hit_tokens'],180)  # 18%, not the mean of 90% and 10%.
        for config,model,purpose in [(cfg,'m','generation'),(cfg,'other','chat'),
                (dict(cfg,base_url='https://other.example'),'m','chat')]:
            usage.record(config,model,purpose,{'usage':{'prompt_tokens':100}})
        self.assertEqual(len(APIUsage(self.c.data).public()),4)
        for malformed in [{}, {'usage':None},{'usage':{'prompt_tokens':True}},'invalid']:
            self.assertIsNone(usage.record(cfg,'m','chat',malformed))

    def test_accounting_write_failure_never_retries_paid_generation(self):
        usage=APIUsage(self.c.data)
        with mock.patch('vampm.ai_usage.atomic_json',side_effect=OSError('disk full')):
            result=usage.record({'provider':'deepseek','base_url':'https://api.deepseek.com'},'m','chat',
                                {'usage':{'prompt_tokens':100,'prompt_cache_hit_tokens':0}})
        self.assertEqual(result['cache_observed_tokens'],100)
