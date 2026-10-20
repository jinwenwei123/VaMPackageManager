import json
import unittest
from unittest import mock

import test_manager as fixtures
import test_summary_queue as queue_fixtures
from test_upgrade import Response
from vampm.ai_response import AIResponseError, completion_json, decode_json
from vampm.providers import AI
from vampm.summary_queue import SummaryQueue
from vampm.util import Cancelled, Job, Jobs


def reply(content, reason='stop', **message):
    return json.dumps({'choices': [{'finish_reason': reason, 'message': {
        'content': content, **message}}], 'usage': {'completion_tokens': 4096,
        'completion_tokens_details': {'reasoning_tokens': 3000}}}).encode()


def result(items):
    return {'items': [{'key': p['key'], 'category': '人物', 'summary': '完整的中文人物外观简介。'}
                      for p in items]}


class ResponseParsing(unittest.TestCase):
    def test_length_rejects_even_valid_partial_json_before_any_save(self):
        for content in ('', '{"items":[{"summary":"未写完', '{"items":[]}'):
            with self.subTest(content=content), self.assertRaises(AIResponseError) as caught:
                completion_json(reply(content, 'length', reasoning_content='private-reasoning'))
            self.assertEqual(caught.exception.details['finish_reason'], 'length')
            self.assertEqual(caught.exception.details['reasoning_tokens'], 3000)
            self.assertNotIn('private-reasoning', str(caught.exception.details))

    def test_exact_unterminated_string_reports_position_without_echoing_content(self):
        malformed = '{"summary":' + ' '*1077 + '"private-body'
        with self.assertRaises(AIResponseError) as caught:
            completion_json(reply(malformed))
        self.assertEqual(caught.exception.details['position'], 1088)
        self.assertEqual(caught.exception.details['column'], 1089)
        self.assertNotIn('private-body', str(caught.exception))
        self.assertIn('JSON', str(caught.exception))

    def test_complete_fenced_json_works_but_invalid_envelopes_do_not(self):
        self.assertEqual(decode_json('```json\n{"items":[]}\n```'), {'items': []})
        for raw in (b'{"choices":[', b'[]', b'{"choices":[]}', b'{"choices":[{}]}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(AIResponseError):
                completion_json(raw)

    def test_refusal_is_not_retryable_or_reported_as_json_damage(self):
        for raw in (reply(None, 'content_filter'), reply(None, refusal='blocked')):
            with self.assertRaises(ValueError) as caught:
                completion_json(raw)
            self.assertNotIsInstance(caught.exception, AIResponseError)
            self.assertIn('不自动重试', str(caught.exception))


class ResponseRecovery(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan
    wait = queue_fixtures.SummaryQueueTests.wait

    def api(self, provider='compatible'):
        ai = AI(self.c)
        ai.save({'provider': provider, 'api_model': 'chat-only', 'api_summary_model': 'deepseek-flash',
                 'base_url': 'https://api.deepseek.com' if provider=='deepseek' else 'https://example.com',
                 'summary_images': False})
        return ai

    def test_official_summary_budget_only_and_diagnostic_has_no_reply_or_secret(self):
        ai = self.api('deepseek'); payloads = []
        def open_request(request, **kwargs):
            payloads.append(json.loads(request.data))
            return Response(reply('{"prompt":"private-body', 'length', reasoning_content='private-thought'))
        with mock.patch.object(ai.models,'get',return_value={'models':[]}), \
             mock.patch('vampm.providers.unprotect_secret', return_value='private-key'), \
             mock.patch('urllib.request.OpenerDirector.open', side_effect=open_request):
            with self.assertRaises(AIResponseError): ai.classify_batch([{'key': 'a.b.1'}], Job('summary'))
            with self.assertRaises(AIResponseError): ai.draft_prompt('推荐人物', '', Job('draft'))
        self.assertEqual(payloads[0]['max_tokens'], 8192)
        self.assertEqual(payloads[0]['reasoning_effort'],'medium')
        self.assertEqual(payloads[1]['max_tokens'], 4096)
        self.assertNotIn('reasoning_effort', payloads[1])
        diagnostic = (self.c.data/'ai-response-diagnostic.json').read_text('utf-8')
        self.assertEqual(json.loads(diagnostic)['finish_reason'], 'length')
        for private in ('private-key', 'private-body', 'private-thought'):
            self.assertNotIn(private, diagnostic)
        ai.save({'base_url': 'https://example.com', 'provider': 'compatible'})
        with mock.patch.object(ai.models,'get',return_value={'models':[]}), \
             mock.patch('vampm.providers.unprotect_secret', return_value='private-key'), \
             mock.patch('urllib.request.OpenerDirector.open', side_effect=open_request):
            with self.assertRaises(AIResponseError): ai.classify_batch([{'key': 'a.b.1'}], Job('summary'))
        self.assertEqual(payloads[-1]['max_tokens'], 4096)
        self.assertNotIn('reasoning_effort', payloads[-1])

    def test_repair_output_budget_does_not_change_other_requests(self):
        ai=self.api('deepseek');payloads=[]
        def open_request(request,**kwargs):
            payloads.append(json.loads(request.data));return Response(reply('{"can_repair":false,"reason":"no","edits":[]}'))
        with mock.patch.object(ai.models,'get',return_value={'models':[]}), mock.patch('vampm.providers.unprotect_secret',return_value='test-key'), mock.patch('urllib.request.OpenerDirector.open',side_effect=open_request):
            cfg=ai.settings(True)
            ai.complete_json(cfg,'repair',{'text':'{}'},{},Job('repair'),'deepseek-flash',output_limit=12288)
            ai.complete_json(cfg,'ordinary',{'text':'{}'},{},Job('ordinary'),'deepseek-flash')
        self.assertEqual([p['max_tokens'] for p in payloads],[12288,4096])

    def test_api_splits_truncated_batches_and_rebuilds_exact_complete_results(self):
        ai=self.api(); sizes=[]
        def open_request(request, **kwargs):
            items=json.loads(json.loads(request.data)['messages'][1]['content']);sizes.append(len(items))
            if len(items)>2:return Response(reply('{"items":[{"summary":"未完', 'length'))
            return Response(reply(json.dumps(result(items))))
        with mock.patch('vampm.providers.unprotect_secret',return_value='test-key'), \
             mock.patch('urllib.request.OpenerDirector.open',side_effect=open_request):
            chunks=list(ai.classification_chunks([{'key':str(i)} for i in range(5)],Job('split')))
        self.assertEqual(sizes,[5,2,3,1,2])
        self.assertEqual({k for chunk in chunks for k in chunk},{str(i) for i in range(5)})
        self.assertTrue(all('完整' in p['summary'] for chunk in chunks for p in chunk.values()))

    def test_single_malformed_reply_retries_once_and_persistent_errors_stop(self):
        ai=self.api();items=[{'key':'a.b.1'}]
        with mock.patch.object(ai,'complete_json',side_effect=[AIResponseError('格式错误'),result(items)]) as call:
            self.assertEqual(len(list(ai.classification_chunks(items,Job('recover')))),1)
            self.assertEqual(call.call_count,2)
        with mock.patch.object(ai,'complete_json',side_effect=AIResponseError('格式错误')) as call:
            with self.assertRaisesRegex(AIResponseError,'自动恢复已停止'):
                list(ai.classification_chunks(items,Job('fail')))
            self.assertEqual(call.call_count,2)

    def test_http_failures_and_cancellation_are_not_retried(self):
        ai=self.api();items=[{'key':'a.b.1'}]
        for error in (ValueError('API 返回 HTTP 429'),Cancelled('已取消')):
            with mock.patch.object(ai,'complete_json',side_effect=error) as call:
                with self.assertRaises(type(error)):list(ai.classification_chunks(items,Job('stop')))
                self.assertEqual(call.call_count,1)
        job=Job('cancel-before-recovery')
        def cancelled(*args,**kwargs):
            job.cancel.set();raise AIResponseError('未完成')
        with mock.patch.object(ai,'complete_json',side_effect=cancelled) as call:
            with self.assertRaises(Cancelled):list(ai.classification_chunks(items,job))
            self.assertEqual(call.call_count,1)

    def test_recovery_budget_is_shared_across_split_branches(self):
        ai=AI(self.c);items=[{'key':str(i)} for i in range(10)]
        def output(cfg,instruction,batch,*args):
            if len(batch)>1:raise AIResponseError('未完成')
            return result(batch)
        with mock.patch.object(ai,'complete_json',side_effect=output) as call:
            completed=[]
            with self.assertRaises(AIResponseError):
                for chunk in ai.classification_chunks(items,Job('bounded')):completed.extend(chunk)
            self.assertLessEqual(call.call_count,7)
            self.assertEqual(completed,['0','1','2'])

    def test_partial_sub_batches_stay_saved_and_resume_only_missing_in_same_run(self):
        for i in range(10):self.put(f'A.Look{i}.1')
        self.scan();ai=self.api();queue=SummaryQueue(self.c,ai,Jobs());seen=[]
        def generate(cfg,instruction,items,*args):
            seen.append([p['key'] for p in items])
            if 'a.look5.1' in seen[-1]:raise AIResponseError('格式错误')
            return result(items)
        with mock.patch.object(ai,'complete_json',side_effect=generate):
            queue.start('all');failed=self.wait(queue)
        self.assertEqual((failed['status'],failed['current'],failed['updated']),('error',0,5))
        pkgs=self.c.packages()
        self.assertTrue(all(pkgs[f'a.look{i}.1']['summary_generated'] for i in range(5)))
        self.assertTrue(all(not pkgs[f'a.look{i}.1']['summary_generated'] for i in range(5,10)))
        queue=SummaryQueue(self.c,ai,Jobs());seen=[]
        def success(cfg,instruction,items,*args):
            seen.extend(p['key'] for p in items);return result(items)
        with mock.patch.object(ai,'complete_json',side_effect=success):
            queue.resume();done=self.wait(queue)
        self.assertEqual((done['status'],done['updated'],done['current']),('done',10,10))
        self.assertEqual(seen,[f'a.look{i}.1' for i in range(5,10)])

    def test_incomplete_keys_are_never_saved_or_marked_generated(self):
        self.put('A.One.1');self.scan();ai=self.api()
        with mock.patch.object(ai,'complete_json',return_value={'items':[]}):
            with self.assertRaises(AIResponseError):
                ai.generate(['a.one.1'],Job('missing-key'))
        self.assertFalse(self.c.packages()['a.one.1']['summary_generated'])
