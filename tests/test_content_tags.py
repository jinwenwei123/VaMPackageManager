import json
import threading
import time
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.catalog import Catalog
from vampm.content_tags import normalize_tags, instruction, VOCABULARY
from vampm.providers import AI
from vampm.summary_queue import SummaryQueue
from vampm.summary_content import INSTRUCTION
from vampm.util import Job, Jobs
from vampm.server import Application
from vampm.agent_tools import invoke
from vampm.agent import text_history
from vampm.ai_response import AIResponseError


class TagTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def wait(self,q):
        end=time.monotonic()+10
        while q.jobs.busy() and time.monotonic()<end:time.sleep(.01)
        self.assertFalse(q.jobs.busy())
        self.assertEqual(q.public()['status'],'done',q.public())

    @staticmethod
    def classify(items,job,target='summary'):
        return {p['key']:{**({'category':'人物','summary':'新的中文人物简介。'} if target!='tags' else {}),
                          **({'tags':['银白发','长发','幻想风','精灵耳','年轻感']} if target!='summary' else {})} for p in items}

    def test_tags_independent_persistent_searchable_and_empty_complete(self):
        path=self.put();self.scan();key='author.scene.1'
        self.c.label(key,summary='保留旧简介',favorite=True)
        before=self.c.detail(key)
        self.c.label(key,tags=['R18','银白发','银白发'],source='ai')
        p=self.c.detail(key)
        self.assertEqual(p['summary'],before['summary']);self.assertEqual(p['category'],before['category'])
        self.assertEqual(p['tags'],['成人向','银白发']);self.assertTrue(p['tags_generated'])
        self.assertTrue(p['favorite']);self.assertEqual(p['source'],before['source'])
        self.assertEqual(self.c.list({'q':'银白发 场景'})['total'],1)
        self.assertEqual(self.c.list({'q':'R18'})['total'],1)
        self.c=Catalog(self.game,self.home);self.scan()
        self.assertTrue(self.c.detail(key)['tags_generated'])
        self.c.label(key,tags=[]);self.assertTrue(self.c.detail(key)['tags_generated'])
        path.write_bytes(fixtures.var_bytes(description='changed'));self.scan()
        self.assertFalse(self.c.detail(key)['tags_generated'])

    def test_both_missing_only_generates_missing_output_and_all_overwrites(self):
        self.put();self.put('A.Second.1');self.scan()
        self.c.label('author.scene.1',summary='保持不动',category='场景')
        self.c.label('a.second.1',tags=['已有标签'])
        ai=AI(self.c);q=SummaryQueue(self.c,ai,Jobs())
        with mock.patch.object(ai,'classify_batch',side_effect=self.classify) as classify:
            q.start('all',target='both',mode='missing');self.wait(q)
            self.assertEqual(q.public()['updated'],2)
            self.assertEqual(self.c.detail('author.scene.1')['summary'],'保持不动')
            self.assertEqual(self.c.detail('author.scene.1')['category'],'场景')
            self.assertEqual(self.c.detail('a.second.1')['tags'],['已有标签'])
            self.assertEqual(classify.call_count,2)
            q.start('all',target='both',mode='missing');self.wait(q)
            self.assertEqual(q.public()['total'],0)
            q.start('all',target='both',mode='all');self.wait(q)
            self.assertEqual(self.c.detail('author.scene.1')['summary'],'新的中文人物简介。')
            self.assertEqual(self.c.detail('a.second.1')['tags'][0],'银白发')
            self.assertEqual(q.public()['updated'],2)

    def test_tags_only_and_summary_only_leave_other_output_untouched(self):
        self.put();self.scan();key='author.scene.1'
        self.c.label(key,summary='保留简介',tags=['先前标签'])
        ai=AI(self.c);q=SummaryQueue(self.c,ai,Jobs())
        with mock.patch.object(ai,'classify_batch',side_effect=self.classify):
            q.start('all',target='tags');self.wait(q)
            self.assertEqual(self.c.detail(key)['summary'],'保留简介')
            tags=self.c.detail(key)['tags']
            q.start('all',target='summary');self.wait(q)
            self.assertEqual(self.c.detail(key)['tags'],tags)

    def test_failed_combined_result_is_atomic_and_resume_skips_saved(self):
        self.put();self.put('A.Second.1');self.scan()
        ai=AI(self.c);q=SummaryQueue(self.c,ai,Jobs())
        original=self.c.label;count=[0]
        def save(key,**kw):
            count[0]+=1
            if count[0]==2:raise ValueError('simulated interruption')
            original(key,**kw)
        with mock.patch.object(ai,'classify_batch',side_effect=self.classify):
            with mock.patch.object(self.c,'label',side_effect=save):
                q.start('all',target='both')
                while q.jobs.busy():time.sleep(.01)
            self.assertEqual(q.public()['status'],'error');self.assertEqual(q.public()['updated'],1)
            with mock.patch.object(ai,'classify_batch',side_effect=self.classify) as classify:
                q.resume();self.wait(q)
                self.assertEqual(len(classify.call_args.args[0]),1)
            self.assertEqual(q.public()['updated'],2)
        with self.assertRaises(AIResponseError):AI.validate({'items':[{'key':'x','category':'人物','summary':'中文','tags':None}]},{'x'},'both')

    def test_schema_and_image_association_each_generation_target(self):
        self.put(files={'Saves/scene/Test.json':'{}','Saves/scene/Test.png':b'\x89PNG\r\n\x1a\nfixture'});self.scan()
        ai=AI(self.c)
        for target in ('summary','tags','both'):
            seen={}
            def complete(cfg,prompt,items,schema,job,model,images):
                seen.update(prompt=prompt,schema=schema,items=items,images=images)
                return {'items':[dict(key=p['key'],**self.classify([p],job,target)[p['key']]) for p in items]}
            with mock.patch.object(ai.models,'supports_images',return_value=True),mock.patch.object(ai,'complete_json',side_effect=complete):
                ai.generate_batch(list(self.c.packages().values()),Job('test'),run_id=target,target=target)
            self.assertEqual(seen['items'][0]['preview_image']['attachment'],1)
            self.assertEqual(seen['images'][0]['key'],'author.scene.1')
            fields=seen['schema']['properties']['items']['items']['properties']
            self.assertEqual('tags' in fields,target!='summary')
            self.assertEqual('summary' in fields,target!='tags')
        self.assertEqual(instruction('summary',INSTRUCTION),INSTRUCTION)
        self.assertGreater(sum(map(len,VOCABULARY.values())),100)

    def test_tag_validation_and_normalization(self):
        self.assertEqual(normalize_tags([' R18 ','少女','幼女','成女','金发','金发']),['成人向','少女','幼女','成女','金发'])
        for tags in (None,['x']*25,['<script>'],[3],['']):
            with self.assertRaises(ValueError):normalize_tags(tags)
        self.assertEqual(AI.validate({'items':[{'key':'x','tags':[]}]},{'x'},'tags'),{'x':{'tags':[]}})

    def test_agent_reads_evidence_and_only_vision_chat_model_receives_image(self):
        self.put(files={'Saves/scene/Test.json':'{}','Saves/scene/Test.png':b'\x89PNG\r\n\x1a\nfixture'});self.scan()
        app=Application(self.game,self.home)
        detail=invoke(app,'inspect_package',{'key':'author.scene.1'})
        self.assertIn('content_evidence',detail);self.assertIn('tags',detail)
        cfg=app.ai.settings(True)
        with mock.patch.object(app.ai.models,'supports_images',return_value=False):
            self.assertNotIn('_images',invoke(app,'view_package_cover',{'key':'author.scene.1'}))
        with mock.patch.object(app.ai.models,'supports_images',return_value=True) as support:
            app.agent.data['read_only']=True
            result=app.agent.call('view_package_cover',{'key':'author.scene.1'})
            self.assertTrue(result['_images'][0]['url'].startswith('data:image/png;base64,'))
            self.assertEqual(support.call_args.kwargs['model'],cfg['codex_model'])
            self.assertNotIn('base64',json.dumps(app.agent.data))
        history=text_history([{'role':'user','content':[{'type':'text','text':'包a'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,secret'}}]}])
        self.assertNotIn('secret',json.dumps(history));self.assertIn('包a',history[0]['content'])

    def test_api_chat_sends_real_image_part_and_does_not_persist_base64(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        self.put(files={'Saves/scene/Test.json':'{}','Saves/scene/Test.png':b'\x89PNG\r\n\x1a\nfixture'});self.scan()
        app=Application(self.game,self.home);requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(payload)
                if len(requests)==1:
                    answer={'role':'assistant','content':None,'tool_calls':[{'id':'cover-1','type':'function',
                        'function':{'name':'view_package_cover','arguments':json.dumps({'key':'author.scene.1'})}}]}
                else:answer={'role':'assistant','content':'已结合封面核对。'}
                body=json.dumps({'choices':[{'message':answer}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        app.ai.save({'provider':'compatible','base_url':f'http://127.0.0.1:{server.server_port}','api_model':'vision-chat','api_summary_model':'text-summary'})
        try:
            with mock.patch('vampm.agent.unprotect_secret',return_value='fixture-key'),mock.patch.object(app.ai.models,'reasoning',return_value=None),mock.patch.object(app.ai.models,'supports_images',return_value=True):
                app.agent.send('检查封面',read_only=True);app.agent.worker.join(timeout=10)
            self.assertEqual(len(requests),2)
            content=requests[1]['messages'][-1]['content']
            self.assertEqual(content[1]['type'],'image_url')
            self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
            self.assertIn('author.scene.1',content[0]['text'])
            self.assertNotIn('base64',app.agent.path.read_text('utf-8'))
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
