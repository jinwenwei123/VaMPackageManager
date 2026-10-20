import json
import threading
import time
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.providers import AI
from vampm.server import Application
from vampm.summary_queue import SummaryQueue
from vampm.util import Job, Jobs, atomic_json


class SummaryQueueTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan

    def queue(self):
        ai = AI(self.c)
        return SummaryQueue(self.c, ai, Jobs())

    def wait(self, queue):
        deadline = time.monotonic() + 15
        while queue.jobs.busy() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(queue.jobs.busy(), 'queue did not finish')
        return queue.public()

    @staticmethod
    def classify(items, job):
        return {p['key']: {'category': '人物', 'summary': p['name'] + ' 人物外观，提供可搭配的角色形象。'} for p in items}

    def test_whole_library_over_200_and_repeat_clicks_skip_existing(self):
        for i in range(213): self.put(f'Author.Look{i:03d}.1')
        self.put('Author.Broken.1', data=b'not a zip'); self.scan()
        self.c.label('author.look000.1', summary='保留 AI 简介', source='ai')
        self.c.label('author.look001.1', summary='保留手动简介')
        queue = self.queue()
        with mock.patch.object(queue.ai, 'classify_batch', side_effect=self.classify) as classify:
            queue.start('all',mode='missing'); result = self.wait(queue)
            self.assertEqual((result['status'], result['total'], result['updated'], result['skipped']), ('done', 211, 211, 3))
            self.assertEqual(classify.call_count, 22)
            self.assertLessEqual(max(len(c.args[0]) for c in classify.call_args_list), 10)
            queue.start('all',mode='missing'); again = self.wait(queue)
            self.assertNotEqual(result['run_id'], again['run_id'])
            self.assertEqual(self.queue().public()['run_id'], again['run_id'])
            self.assertEqual(again['updated'], 0)
            self.assertEqual(classify.call_count, 22)
        self.assertNotIn('keys', queue.public())
        self.assertEqual(self.c.packages()['author.look000.1']['summary'], '保留 AI 简介')
        self.assertEqual(self.c.packages()['author.look001.1']['summary'], '保留手动简介')

    def test_pause_finishes_batch_and_resume_rechecks_favorites_and_manual_labels(self):
        for i in range(23): self.put(f'A.Look{i:02d}.1')
        self.scan()
        for i in range(22): self.c.label(f'a.look{i:02d}.1', favorite=True)
        queue = self.queue(); entered, release = threading.Event(), threading.Event()
        seen = []
        def classify(items, job):
            seen.extend(p['key'] for p in items)
            entered.set(); self.assertTrue(release.wait(10))
            return self.classify(items, job)
        with mock.patch.object(queue.ai, 'classify_batch', side_effect=classify):
            queue.start('favorites',mode='missing')
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(queue.pause()['status'], 'pausing')
                self.assertTrue(queue.jobs.busy())
            finally: release.set()
            paused = self.wait(queue)
        self.assertEqual((paused['status'], paused['current'], paused['updated']), ('paused', 10, 10))
        self.assertEqual(len(seen), 10)
        self.c.label('a.look10.1', summary='暂停期间手动整理')
        self.c.label('a.look11.1', favorite=False)
        queue = self.queue()  # Reopening also keeps the paused queue.
        with mock.patch.object(queue.ai, 'classify_batch', side_effect=self.classify) as classify:
            queue.resume(); result = self.wait(queue)
            keys = [p['key'] for call in classify.call_args_list for p in call.args[0]]
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['run_id'], paused['run_id'])
        self.assertEqual((result['current'], result['updated'], result['skipped']), (22, 20, 2))
        self.assertTrue(set(seen).isdisjoint(keys))
        self.assertNotIn('a.look11.1', keys)
        self.assertNotIn('a.look22.1', keys)
        self.assertEqual(self.c.packages()['a.look10.1']['summary'], '暂停期间手动整理')

    def test_interrupted_checkpoint_recovers_paused_without_repeating_saved_labels(self):
        self.put('A.One.1'); self.put('A.Two.1'); self.scan()
        self.c.label('a.one.1', summary='崩溃前已保存', source='ai')
        atomic_json(self.c.data/'summary-queue.json', {'status': 'running', 'scope': 'all',
                    'keys': ['a.one.1', 'a.two.1'], 'current': 0, 'total': 2, 'updated': 0, 'skipped': 0})
        queue = self.queue()
        self.assertEqual(queue.public()['status'], 'paused')
        self.assertFalse(queue.jobs.busy())
        with mock.patch.object(queue.ai, 'classify_batch', side_effect=self.classify) as classify:
            queue.resume(); self.wait(queue)
            self.assertEqual([p['key'] for p in classify.call_args.args[0]], ['a.two.1'])
        self.assertEqual(self.c.packages()['a.one.1']['summary'], '崩溃前已保存')

    def test_provider_error_keeps_previous_batches_and_manual_retry(self):
        for i in range(12): self.put(f'A.Look{i:02d}.1')
        self.scan(); queue = self.queue(); calls = []
        def classify(items, job):
            calls.append([p['key'] for p in items])
            if len(calls) == 2: raise ValueError('模拟额度不足')
            return self.classify(items, job)
        with mock.patch.object(queue.ai, 'classify_batch', side_effect=classify):
            queue.start('all'); failed = self.wait(queue)
            self.assertEqual((failed['status'], failed['current']), ('error', 10))
            self.assertIn('模拟额度不足', failed['error'])
            queue.resume(); done = self.wait(queue)
        self.assertEqual((done['status'], done['updated']), ('done', 12))
        self.assertEqual(calls[1], calls[2])
        self.assertTrue(set(calls[0]).isdisjoint(calls[2]))

    def test_selected_scope_deduplicates_and_only_single_explicit_reanalysis_overwrites(self):
        self.put('A.One.1'); self.put('A.Two.1'); self.scan(); queue = self.queue()
        self.c.label('a.one.1', summary='已生成的简介', source='ai')
        with mock.patch.object(queue.ai, 'classify_batch', side_effect=self.classify) as classify:
            queue.start('selected', ['A.One.1', 'a.one.1', 'a.two.1'],mode='missing'); result = self.wait(queue)
            self.assertEqual((result['updated'], result['skipped']), (1, 1))
            queue.ai.generate(['a.one.1', 'a.two.1'], Job('ai'))
            self.assertEqual(classify.call_count, 1)
            queue.ai.generate(['a.one.1'], Job('ai'), overwrite=True)
            self.assertEqual(classify.call_count, 2)
            with self.assertRaisesRegex(ValueError, '单个包'):
                queue.ai.generate(['a.one.1', 'a.two.1'], Job('ai'), overwrite=True)
            with self.assertRaisesRegex(ValueError, '单个包'):
                queue.ai.generate(['a.one.1'], Job('ai'), refresh_ai=True)

    def test_busy_start_rolls_back_and_pending_queue_cannot_be_replaced(self):
        self.put(); self.scan(); queue = self.queue(); release = threading.Event()
        queue.jobs.start('other', lambda j: release.wait(10))
        try:
            with self.assertRaises(ValueError): queue.start('all')
            self.assertNotIn('status', queue.public())
        finally: release.set(); self.wait(queue)
        queue.state = {'status': 'paused', 'scope': 'all', 'keys': ['author.scene.1'],
                       'current': 0, 'total': 1, 'updated': 0, 'skipped': 0}
        queue._save()
        with self.assertRaises(ValueError): queue.start('favorites')
        queue.finish()
        self.assertEqual(json.loads(queue.file.read_text('utf-8'))['status'], 'cancelled')

    def test_api_pause_allowed_during_job_and_browsing_remains_available(self):
        for i in range(11): self.put(f'A.Look{i:02d}.1')
        self.scan(); app = Application(self.game, self.home)
        entered, release = threading.Event(), threading.Event()
        def classify(items, job):
            entered.set(); self.assertTrue(release.wait(10)); return self.classify(items, job)
        with mock.patch.object(app.ai, 'classify_batch', side_effect=classify):
            app.post('/api/ai-queue/start', {'scope': 'all'})
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(app.get('/api/packages', {})['total'], 11)
                self.assertNotIn('keys', app.get('/api/status', {})['summary_queue'])
                self.assertEqual(app.post('/api/ai-queue/pause', {})['status'], 'pausing')
                with self.assertRaises(ValueError): app.post('/api/label', {'key': 'a.look00.1', 'summary': '手动'})
            finally: release.set(); self.wait(app.summaries)
        self.assertEqual(app.get('/api/status', {})['summary_queue']['status'], 'paused')
        self.assertEqual(app.post('/api/ai-queue/finish', {})['status'], 'cancelled')

    def test_cancel_retains_saved_batches_and_next_round_only_fills_missing(self):
        for i in range(12): self.put(f'A.Look{i:02d}.1')
        self.scan(); queue = self.queue(); calls = []
        def classify(items, job):
            calls.append([p['key'] for p in items])
            if len(calls) == 2: job.cancel.set(); job.check()
            return self.classify(items, job)
        with mock.patch.object(queue.ai, 'classify_batch', side_effect=classify):
            queue.start('all'); cancelled = self.wait(queue)
            self.assertEqual((cancelled['status'], cancelled['current']), ('cancelled', 10))
            queue.start('all',mode='missing'); done = self.wait(queue)
        self.assertEqual((done['updated'], done['skipped']), (2, 10))
        self.assertEqual(calls[1], calls[2])

    def test_default_all_rewrites_completed_and_manual_summaries_each_new_run(self):
        self.put('A.One.1');self.put('A.Two.1');self.scan()
        self.c.label('a.one.1',summary='原 AI 简介',source='ai',favorite=True)
        self.c.label('a.two.1',summary='原手动简介')
        queue=self.queue();calls=[]
        def classify(items,job):
            calls.append([p['key'] for p in items])
            return {p['key']:{'category':'人物','summary':f'第 {len(calls)} 轮的完整人物简介。'} for p in items}
        with mock.patch.object(queue.ai,'classify_batch',side_effect=classify):
            queue.start('all');first=self.wait(queue)
            self.assertEqual((first['mode'],first['updated'],first['skipped']),('all',2,0))
            queue.start('all');second=self.wait(queue)
        self.assertEqual(second['updated'],2);self.assertEqual(calls,[['a.one.1','a.two.1']]*2)
        self.assertTrue(self.c.packages()['a.one.1']['favorite'])
        self.assertEqual(self.c.packages()['a.two.1']['summary'],'第 2 轮的完整人物简介。')

    def test_all_mode_partial_save_then_restart_skips_only_this_runs_saved_results(self):
        for name in ['One','Two','Three']:self.put('A.'+name+'.1')
        self.scan()
        for key in self.c.packages():self.c.label(key,summary='旧简介',source='ai')
        queue=self.queue();original=self.c.label;calls=[]
        def fail(key,**kw):
            calls.append(key)
            if len(calls)==2:raise OSError('模拟保存中断')
            return original(key,**kw)
        with mock.patch.object(queue.ai,'classify_batch',side_effect=self.classify),mock.patch.object(self.c,'label',side_effect=fail):
            queue.start('all');failed=self.wait(queue)
        self.assertEqual((failed['status'],failed['current'],failed['updated']),('error',0,1))
        saved=calls[0];queue=self.queue()
        with mock.patch.object(queue.ai,'classify_batch',side_effect=self.classify) as classify:
            queue.resume();result=self.wait(queue)
        retried=[p['key'] for c in classify.call_args_list for p in c.args[0]]
        self.assertNotIn(saved,retried);self.assertEqual(len(retried),2)
        self.assertEqual((result['updated'],result['skipped'],result['current']),(3,0,3))

    def test_all_mode_saved_batch_before_checkpoint_does_not_call_model_again(self):
        self.put('A.One.1');self.put('A.Two.1');self.scan()
        for key in self.c.packages():self.c.label(key,summary='本轮已保存',source='ai',summary_run_id='interrupted-run')
        atomic_json(self.c.data/'summary-queue.json',{'status':'running','scope':'all','mode':'all','run_id':'interrupted-run',
            'keys':['a.one.1','a.two.1'],'current':0,'total':2,'updated':0,'skipped':0})
        queue=self.queue()
        with mock.patch.object(queue.ai,'classify_batch',side_effect=AssertionError('重复消耗额度')):
            queue.resume();result=self.wait(queue)
        self.assertEqual((result['status'],result['updated'],result['skipped']),('done',2,0))

    def test_invalid_mode_does_not_replace_queue_or_launch_inference(self):
        self.put();self.scan();queue=self.queue()
        with self.assertRaises(ValueError):queue.start('all',mode='unknown')
        self.assertFalse(queue.state);self.assertFalse(queue.jobs.busy())

    def completed_queue(self):
        queue = self.queue(); self.addCleanup(queue.ai.close)
        queue.state = {'status': 'done', 'scope': 'selected', 'mode': 'all', 'target': 'both',
                       'run_id': 'finished-run', 'keys': ['author.scene.1'], 'current': 1,
                       'total': 1, 'updated': 1, 'skipped': 0, 'message': 'completed'}
        queue._save()
        return queue

    def test_dismissal_persists_after_restart_without_removing_results_and_new_run_is_visible(self):
        queue = self.completed_queue(); before = dict(queue.state)
        public = queue.dismiss('finished-run')
        self.assertTrue(public['notice_dismissed']); self.assertNotIn('keys', public)
        self.assertEqual({k: queue.state[k] for k in before if k != 'modified'},
                         {k: v for k, v in before.items() if k != 'modified'})
        reopened = self.queue(); self.addCleanup(reopened.ai.close)
        self.assertTrue(reopened.public()['notice_dismissed'])
        self.assertEqual(reopened.state['keys'], ['author.scene.1'])
        checkpoint = reopened.file.read_bytes()
        reopened.dismiss('finished-run')
        self.assertEqual(reopened.file.read_bytes(), checkpoint)  # Duplicate close is idempotent.
        with mock.patch.object(reopened.ai, 'generate_batch', side_effect=AssertionError('unexpected inference')):
            reopened.start('all')  # Empty new run; no model call is needed.
            result = self.wait(reopened)
        self.assertEqual(result['status'], 'done'); self.assertFalse(result.get('notice_dismissed'))
        self.assertNotEqual(result['run_id'], 'finished-run')
        with self.assertRaises(ValueError): reopened.dismiss('finished-run')
        self.assertFalse(reopened.public().get('notice_dismissed'))

    def test_incomplete_or_stale_queue_cannot_be_dismissed(self):
        queue = self.completed_queue()
        for status in ('running', 'pausing', 'paused', 'error', 'cancelled'):
            queue.state['status'] = status; queue._save(); before = queue.file.read_bytes()
            with self.subTest(status=status), self.assertRaises(ValueError): queue.dismiss('finished-run')
            self.assertEqual(queue.file.read_bytes(), before)
            self.assertFalse(queue.public().get('notice_dismissed'))
        queue.state['status'] = 'done'; queue._save()
        for run in (None, '', {}, 'another-run'):
            with self.subTest(run=run), self.assertRaises(ValueError): queue.dismiss(run)
        self.assertFalse(queue.public().get('notice_dismissed'))

    def test_failed_dismissal_save_keeps_notice_and_saved_queue(self):
        queue = self.completed_queue(); before = dict(queue.state); checkpoint = queue.file.read_bytes()
        with mock.patch('vampm.summary_queue.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): queue.dismiss('finished-run')
        self.assertEqual(queue.state, before); self.assertEqual(queue.file.read_bytes(), checkpoint)

    def test_dismissal_api_is_per_game_and_can_run_during_an_unrelated_job(self):
        queue = self.completed_queue()
        app = Application(self.game, self.home)
        self.addCleanup(lambda: app.active.ai.close()); self.addCleanup(lambda: app.active.agent.close())
        app.jobs.lock.acquire()
        try:
            self.assertTrue(app.post('/api/ai-queue/dismiss', {'run_id': 'finished-run'})['notice_dismissed'])
        finally: app.jobs.lock.release()
        other = self.root / 'other'; (other / 'AddonPackages').mkdir(parents=True)
        ident = app.post('/api/game-directories/add', {'path': str(other)})['items'][-1]['id']
        app.post('/api/game-directories/switch', {'id': ident})
        app.active.summaries.state = dict(queue.state); app.active.summaries._save()
        self.assertFalse(app.get('/api/status', {})['summary_queue'].get('notice_dismissed'))
        app.post('/api/ai-queue/dismiss', {'run_id': 'finished-run'})
        app.post('/api/game-directories/switch', {'id': 'original'})
        self.assertTrue(app.get('/api/status', {})['summary_queue']['notice_dismissed'])
