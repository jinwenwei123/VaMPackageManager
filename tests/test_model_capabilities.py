import json
import threading
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.model_catalog import model_info
from vampm.model_capabilities import resolve, overrides, validate_overrides
from vampm.providers import AI
from vampm.util import Cancelled


class Capabilities(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_supported_parameters_describe_capabilities_without_inventing_effort_levels(self):
        info=model_info({'id':'vendor/model','supported_parameters':['tools','response_format','reasoning'],
                         'architecture':{'input_modalities':['text','image']},'context_length':131072})
        for key in ('vision','reasoning','tool_calling','json_output'):
            self.assertTrue(info[key]);self.assertEqual(info['capability_sources'][key]['kind'],'api')
        self.assertEqual(info['reasoning_efforts'],[])
        self.assertEqual(info['tags'],['识图','推理','工具调用','JSON 输出','128K 上下文'])
        ai=AI(self.c);cfg=ai.settings(True);cfg['provider']='compatible'
        with mock.patch.object(ai.models,'get',return_value={'models':[info]}):
            self.assertIsNone(ai.models.reasoning(cfg,info['id'],threading.Event()))

    def test_explicit_false_is_not_replaced_by_parameter_hints_and_missing_is_unknown(self):
        info=model_info({'id':'x','capabilities':dict(vision=False,reasoning=False,tool_calling=False,json_output=False),
                         'supported_parameters':['tools','structured_outputs','reasoning_effort']})
        for field in ('vision','reasoning','tool_calling','json_output'): self.assertIs(info[field],False)
        unknown=model_info({'id':'reasoning-vision-tools-in-name','capabilities':{'vision':'true','reasoning':1},
                            'supported_parameters':{'tools':True}})
        for field in ('vision','reasoning','tool_calling','json_output'): self.assertIsNone(unknown[field])

    def test_official_evidence_matches_only_exact_endpoint_and_model_and_fills_missing_fields(self):
        base='https://api.siliconflow.cn/v1'
        model='Qwen/Qwen2.5-VL-72B-Instruct'
        raw=model_info({'id':model})
        found=resolve(raw,base+'/chat/completions/')
        self.assertTrue(found['vision']);self.assertEqual(found['capability_sources']['vision']['kind'],'official')
        self.assertIn('multimodal-vision',found['capability_sources']['vision']['url'])
        self.assertIsNone(raw['vision'])  # Never mutate the cached API facts.
        for other in ('https://relay.example/v1','https://api.siliconflow.cn.fake/v1','https://api.siliconflow.cn/other'):
            self.assertIsNone(resolve(raw,other)['vision'])
        self.assertIsNone(resolve(model_info({'id':model+'-custom'}),base)['vision'])
        self.assertIsNone(resolve(model_info({'id':'deepseek-ai/DeepSeek-V4.1-Flash'}),base)['reasoning'])
        declared=resolve(model_info({'id':model,'input_modalities':['text']}),base)
        self.assertIs(declared['vision'],False);self.assertEqual(declared['capability_sources']['vision']['kind'],'api')
        manual=resolve(declared,base,{'vision':True})
        self.assertTrue(manual['vision']);self.assertEqual(manual['capability_sources']['vision']['kind'],'manual')

    def test_corrections_persist_per_endpoint_and_exact_model_and_reset_to_automatic(self):
        ai=AI(self.c);base='https://api.siliconflow.cn/v1';model='private/vision'
        ai.save({'base_url':base,'api_summary_model':model,'api_capability_overrides':{model:{'vision':True}}})
        cfg=ai.settings(True);cancel=threading.Event()
        with mock.patch.object(ai.models,'get',side_effect=ValueError('offline')) as get:
            self.assertTrue(ai.models.supports_images(dict(cfg,provider='compatible'),cancel))
            get.assert_not_called()
            self.assertFalse(ai.models.supports_images(dict(cfg,provider='compatible'),cancel,model+'-other'))
        # Restart/reload and canonical endpoint preserve the same correction.
        ai=AI(self.c)
        ai.save({'base_url':base+'/chat/completions/'})
        self.assertEqual(ai.settings()['api_capability_overrides'],{model:{'vision':True}})
        ai.save({'base_url':'https://API.SILICONFLOW.CN:443/v1/'})
        self.assertEqual(ai.settings()['api_capability_overrides'],{model:{'vision':True}})
        ai.save({'base_url':'https://other.example/v1'})
        self.assertEqual(ai.settings()['api_capability_overrides'],{})
        ai.save({'api_capability_overrides':{model:{'vision':False}}})
        self.assertIs(overrides(ai.settings(True))[model]['vision'],False)
        ai.save({'base_url':base})
        self.assertTrue(overrides(ai.settings(True))[model]['vision'])
        ai.save({'api_capability_overrides':{}})
        self.assertEqual(overrides(ai.settings(True)),{})
        self.assertNotIn('model_capability_overrides',ai.settings())
        self.assertNotIn('secret',ai.settings())
        cancel.set()
        with self.assertRaises(Cancelled):ai.models.supports_images(dict(cfg,provider='compatible'),cancel)

    def test_official_vision_works_without_catalog_and_manual_disable_takes_precedence(self):
        ai=AI(self.c);model='Qwen/Qwen2.5-VL-72B-Instruct'
        ai.save({'provider':'compatible','base_url':'https://api.siliconflow.cn/v1','api_summary_model':model})
        with mock.patch.object(ai.models,'get',side_effect=ValueError('no models')):
            self.assertTrue(ai.models.supports_images(ai.settings(True),threading.Event()))
            ai.save({'api_capability_overrides':{model:{'vision':False}}})
            self.assertFalse(ai.models.supports_images(ai.settings(True),threading.Event()))

    def test_manual_and_official_sources_never_pollute_api_cache(self):
        ai=AI(self.c);model='Qwen/Qwen2.5-VL-72B-Instruct'
        ai.save({'base_url':'https://api.siliconflow.cn/v1','api_capability_overrides':{model:{'vision':False}}})
        with mock.patch.object(ai.models,'api',return_value=[{'id':model}]) as fetch:
            for _ in range(2):
                row=ai.models.get('api')['models'][0]
                self.assertTrue(row['vision']);self.assertEqual(row['capability_sources']['vision']['kind'],'official')
            self.assertEqual(fetch.call_count,1)
        cached=json.loads(ai.models.file.read_text('utf-8'))
        stored=next(iter(cached.values()))['models'][0]
        self.assertIsNone(stored['vision']);self.assertNotIn('official',json.dumps(cached));self.assertNotIn('manual',json.dumps(cached))
        with mock.patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('settings must not fetch')):
            row=ai.settings()['api_model_capabilities'][ai.settings()['api_model']]
            self.assertIsNone(row['vision'])

    def test_invalid_corrections_are_rejected_without_partial_settings_writes(self):
        ai=AI(self.c);ai.save({'provider':'compatible'});original=ai.file.read_bytes()
        invalid=[[],{'x':{'vision':'true'}},{'x':{'vision':1}},{'x':{'max_tokens':100}},
                 {'':{'vision':True}},{' x':{'vision':True}},{'x':None},
                 {f'm{i}':{'vision':True} for i in range(201)}]
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):ai.save({'api_capability_overrides':value})
                self.assertEqual(ai.file.read_bytes(),original)
        self.assertEqual(validate_overrides({'x':{}}),{})

    def test_inflight_generation_uses_its_own_endpoint_snapshot_for_capabilities(self):
        ai=AI(self.c);model='same-id'
        ai.save({'provider':'compatible','base_url':'https://first.example/v1','api_summary_model':model})
        first=ai.settings(True)
        ai.save({'base_url':'https://second.example/v1'})
        def catalog(cfg,cancel):
            self.assertEqual(cfg['base_url'],'https://first.example/v1')
            return [{'id':model,'input_modalities':['text']}]
        with mock.patch.object(ai.models,'api',side_effect=catalog):
            self.assertFalse(ai.models.supports_images(first,threading.Event()))
