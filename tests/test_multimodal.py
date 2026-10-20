import base64
import io
import json
import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.importer import Importer
from vampm.providers import AI, Downloads
from vampm.summary_content import prepare_inputs
from vampm.util import Job


class MultimodalSummaries(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def hair(self):
        return self.put('A.Hair.1',files={'Custom/Hair/Female/A/Test.vam':'{}',
            'Custom/Hair/Female/A/Test.jpg':b'\xff\xd8\xffpreview',
            'Custom/Hair/Female/A/Test_A.jpg':b'\xff\xd8\xffalpha',
            'Custom/Textures/cover.jpg':b'\xff\xd8\xfftexture'})

    def test_preview_is_paired_bounded_and_mapped_without_local_paths(self):
        path=self.hair()
        items=[{'key':'no.image.1','name':'Empty'}, {'key':'a.hair.1','_archive_path':path}]
        public,images=prepare_inputs(items,True,Job('test'))
        self.assertNotIn('preview_image',public[0])
        self.assertEqual(public[1]['preview_image']['attachment'],1)
        self.assertEqual(images[0]['data'],b'\xff\xd8\xffpreview')
        self.assertNotIn(str(path),json.dumps(public))
        self.assertEqual(items[1]['_archive_path'],path)
        with mock.patch('vampm.summary_content.read_cover',side_effect=AssertionError('images disabled')):
            public,images=prepare_inputs(items,False,Job('test'))
        self.assertFalse(images);self.assertNotIn('preview_image',public[1])
        with self.assertRaises(ValueError):prepare_inputs(items*6,True,Job('test'))

    def test_missing_or_corrupt_preview_keeps_text_evidence(self):
        path=self.put(data=b'not a zip')
        items=[{'key':'a.scene.1','_archive_path':path,'name':'Test'}]
        public,images=prepare_inputs(items,True,Job('test'))
        self.assertEqual(public,[{'key':'a.scene.1','name':'Test'}]);self.assertFalse(images)

    def test_codex_gets_real_images_and_cleanup_on_completion(self):
        path=self.hair();ai=AI(self.c);folders=[]
        def spawn(command,**kwargs):
            folder=Path(command[command.index('-C')+1]);folders.append(folder)
            image=Path(command[command.index('--image')+1])
            self.assertEqual(image.read_bytes(),b'\xff\xd8\xffpreview')
            prompt=kwargs['stdin'].read().decode('utf-8')
            self.assertIn('preview-01.jpg',prompt);self.assertNotIn(str(path),prompt)
            self.assertEqual(command[command.index('-m')+1],'gpt-6-luna')
            result={'items':[{'key':'a.hair.1','category':'头发','summary':'短发资源。'}]}
            Path(command[command.index('-o')+1]).write_text(json.dumps(result),'utf-8')
            return mock.Mock(returncode=0,poll=lambda:0)
        with mock.patch.object(ai.models,'get',return_value={'models':[]}),mock.patch('vampm.providers.find_codex',return_value='codex'),mock.patch.object(ai,'spawn',side_effect=spawn),mock.patch.object(ai.models,'supports_images',return_value=True):
            self.assertEqual(ai.classify_batch([{'key':'a.hair.1','_archive_path':path}],Job('test'))['a.hair.1']['summary'],'短发资源。')
        self.assertTrue(folders);self.assertFalse(folders[0].exists())

    def test_compatible_vision_payload_and_text_only_default(self):
        path=self.hair();ai=AI(self.c);ai.save({'provider':'compatible','api_model':'chat-model','api_summary_model':'vision-model'})
        payloads=[]
        def open_request(request,**kwargs):
            payloads.append(json.loads(request.data))
            self.assertEqual(payloads[-1]['model'],'vision-model')
            content=json.dumps({'items':[{'key':'a.hair.1','category':'头发','summary':'短发资源。'}]})
            return Response(json.dumps({'choices':[{'message':{'content':content}}]}).encode())
        with mock.patch('vampm.providers.unprotect_secret',return_value='test-key'),mock.patch('urllib.request.OpenerDirector.open',side_effect=open_request),mock.patch.object(ai.models,'supports_images',side_effect=[False,True]):
            ai.classify_batch([{'key':'a.hair.1','_archive_path':path}],Job('text'))
            self.assertIsInstance(payloads[-1]['messages'][1]['content'],str)
            ai.classify_batch([{'key':'a.hair.1','_archive_path':path}],Job('vision'))
        content=payloads[-1]['messages'][1]['content']
        self.assertIn('a.hair.1',content[1]['text'])
        url=content[2]['image_url']['url']
        self.assertEqual(base64.b64decode(url.split(',',1)[1]),b'\xff\xd8\xffpreview')
        self.assertNotIn(str(path),json.dumps(payloads))

    def test_short_summary_removes_uncertainty_without_inventing_details(self):
        def clean(text):return AI.validate({'items':[{'key':'a','category':'头发','summary':text}]},{'a'})['a']['summary']
        self.assertEqual(clean('230604 发型资源；仅凭现有信息无法确认发长与造型。'),'230604 发型资源。')
        self.assertEqual(clean('齐肩直发。具体造型不明。'),'齐肩直发。')
        self.assertEqual(clean('无法确认发长。'),'头发资源。')
        self.assertEqual(clean('短发资源。'),'短发资源。')
        self.assertEqual(clean('内含短发与独立刘海；二者可分别调整。'),'内含短发与独立刘海；二者可分别调整。')

    def test_import_and_indexed_reanalysis_supply_the_archive(self):
        path=self.hair();self.scan();ai=AI(self.c)
        def classify(items,job):
            public,images=prepare_inputs(items,True,job)
            self.assertEqual(len(images),1)
            return {items[0]['key']:{'category':'头发','summary':'双马尾发型，搭配侧分刘海。'}}
        with mock.patch.object(ai,'classify_batch',side_effect=classify):
            ai.generate(['a.hair.1'],Job('indexed'),overwrite=True)
            data=fixtures.var_bytes('New.Hair.1',files={'Custom/Hair/Female/New.vam':'{}','Custom/Hair/Female/New.png':b'\x89PNG\r\n\x1a\npreview'})
            importer=Importer(self.c,ai,Downloads(self.c))
            upload=importer.upload(io.BytesIO(data),'New.Hair.1.var',len(data))
            result=importer.run([upload['id']],Job('import'),use_ai=True,find=False)
        self.assertEqual(result['installed'],1);self.assertFalse(result['ai_error'])
        self.assertEqual(self.c.packages()['new.hair.1']['summary'],'双马尾发型，搭配侧分刘海。')
        self.assertTrue(self.c.packages()['new.hair.1']['summary_generated'])
        self.assertTrue(self.c.packages()['a.hair.1']['summary_generated'])
