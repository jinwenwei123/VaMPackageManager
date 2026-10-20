import json
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from unittest import mock

import test_manager as fixtures
from vampm.lifecycle import PageSessions
from vampm.operations import move_no_replace
from vampm.server import Application, serve
from vampm.util import Cancelled, Job, cancellable_read


class PageSessionTests(unittest.TestCase):
    def test_multi_tab_background_refresh_and_stale_disconnect(self):
        clock=[0];exits=[]
        pages=PageSessions(lambda:exits.append(True),clock=lambda:clock[0])
        first=pages.open('first-page');second=pages.open('second-page')
        clock[0]=100000  # An intact background connection never expires.
        self.assertFalse(pages.check())
        pages.close('first-page',first);self.assertFalse(pages.check())
        pages.close('second-page',second);clock[0]+=4
        self.assertFalse(pages.check())
        refreshed=pages.open('second-page')
        pages.close('second-page',second)  # Delayed old stream cannot close its replacement.
        clock[0]+=20;self.assertFalse(pages.check())
        pages.close('second-page',refreshed);clock[0]+=5
        self.assertTrue(pages.check());self.assertEqual(exits,[True])
        self.assertFalse(pages.check())
        with self.assertRaises(ValueError): pages.open('third-page')

    def test_startup_timeout_and_exe_reopen_grace(self):
        clock=[0];exits=[]
        pages=PageSessions(lambda:exits.append(True),clock=lambda:clock[0])
        clock[0]=89;self.assertFalse(pages.check())
        pages.launching();clock[0]=100;self.assertFalse(pages.check())
        clock[0]=180;self.assertTrue(pages.check())
        self.assertEqual(len(exits),1)


class LifecycleTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def test_http_disconnect_exits_without_close_notification(self):
        app=Application(self.game,self.home);app.pages.grace=.05
        server=serve(app);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        app.pages.start()
        url=f'http://127.0.0.1:{server.server_port}/api/ui-session?id=lost-page'
        try:
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(url,timeout=2)
            self.assertEqual(len(app.pages.sessions),0)
            response=urllib.request.urlopen(urllib.request.Request(url,headers={'X-Session-Token':app.token}),timeout=5)
            self.assertTrue(response.readline().startswith(b': page-connected'))
            self.assertEqual(len(app.pages.sessions),1)
            response.close()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(app.closing.is_set())
        finally:
            app.pages.stop();server.shutdown();server.server_close();worker.join()

    def test_shutdown_suspends_queue_and_next_app_can_continue(self):
        for i in range(12): self.put(f'A.Look{i:02d}.1')
        self.scan();app=Application(self.game,self.home);second=threading.Event();calls=[]
        def classify(items,job):
            calls.append([p['key'] for p in items])
            if len(calls)==2:
                second.set();self.assertTrue(job.cancel.wait(5));job.check()
            return {p['key']:{'category':'人物','summary':'人物外观，可用于搭配。'} for p in items}
        with mock.patch.object(app.ai,'classify_batch',side_effect=classify):
            app.post('/api/ai-queue/start',{'scope':'all'})
            self.assertTrue(second.wait(5))
            app.post('/api/shutdown',{})
            app.close_worker.join(timeout=5)
            self.assertFalse(app.close_worker.is_alive())
        self.assertEqual(app.summaries.public()['status'],'paused')
        self.assertEqual(app.summaries.public()['updated'],10)
        self.assertFalse(app.jobs.busy())
        with self.assertRaises(ValueError):app.post('/api/ai-queue/resume',{})
        restored=Application(self.game,self.home)
        self.assertEqual(restored.summaries.public()['status'],'paused')
        with mock.patch.object(restored.ai,'classify_batch',side_effect=classify):
            restored.post('/api/ai-queue/resume',{});restored.jobs.wait()
        self.assertEqual(restored.summaries.public()['updated'],12)
        self.assertEqual(calls[1],calls[2])

    def test_shutdown_waits_for_move_journal_and_all_files_remain_recoverable(self):
        for i in range(3):self.put(f'A.Look{i}.1')
        self.scan();app=Application(self.game,self.home)
        plan=app.ops.plan('organize',list(app.c.packages()),Job('plan'))
        entered,release,exited=threading.Event(),threading.Event(),threading.Event()
        app.server=mock.Mock();app.server.shutdown.side_effect=exited.set
        def slow_move(src,dst,job=None):
            move_no_replace(src,dst);entered.set();self.assertTrue(release.wait(5))
        with mock.patch('vampm.operations.move_no_replace',side_effect=slow_move):
            app.post('/api/apply',{'id':plan['id']})
            try:
                self.assertTrue(entered.wait(5));app.request_shutdown()
                self.assertFalse(exited.wait(.1))
            finally:release.set();app.close_worker.join(timeout=5)
        self.assertTrue(exited.is_set())
        records=app.ops.history()
        self.assertEqual(records[0]['status'],'interrupted')
        self.assertTrue(all(app.c.path(row).is_file() for row in app.c.rows()))
        self.assertEqual(app.ops.undo(plan['id'],Job('restore'))['restored'],1)
        self.assertTrue(all('/' not in row['rel'] for row in app.c.rows()))

    def test_read_only_inference_wait_is_cancellable(self):
        cancel,release,entered=threading.Event(),threading.Event(),threading.Event();out=[]
        def fetch():entered.set();release.wait(5);return b'response'
        def wait():
            try:cancellable_read(fetch,cancel)
            except Cancelled:out.append('cancelled')
        thread=threading.Thread(target=wait);thread.start()
        try:
            self.assertTrue(entered.wait(2));cancel.set();thread.join(timeout=1)
            self.assertEqual(out,['cancelled']);self.assertFalse(thread.is_alive())
        finally:release.set();thread.join()
        self.assertEqual(cancellable_read(lambda:b'normal',threading.Event()),b'normal')

    def test_owned_ai_process_stops_without_terminating_unrelated_process(self):
        app=Application(self.game,self.home)
        owned=app.ai.spawn([sys.executable,'-c','import time; time.sleep(30)'])
        unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        try:
            app.request_shutdown();app.close_worker.join(timeout=10)
            self.assertFalse(app.close_worker.is_alive());self.assertIsNotNone(owned.poll())
            self.assertIsNone(unrelated.poll())
            with self.assertRaises(Cancelled):app.ai.spawn([sys.executable,'-c','pass'])
        finally:
            for p in (owned,unrelated):
                if p.poll() is None:p.terminate()
                p.wait(timeout=5)
