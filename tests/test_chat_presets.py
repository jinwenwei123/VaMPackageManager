import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import test_manager as fixtures
from vampm.chat_presets import ChatPresets, DEFAULTS, DEFAULTS_EN
from vampm.language import Language
from vampm.server import Application, serve


class Presets(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def change(self,presets,**data):
        return presets.save({'revision':presets.get()['revision'],**data})

    def test_builtin_localization_preserves_legacy_storage_and_unchanged_editor_saves(self):
        presets=ChatPresets(self.c.data)
        self.change(presets,action='feature',id='people',featured=False)
        disk=presets.file.read_bytes()
        language=Language(self.c.data)
        language.save({'language':'en'})
        presets=ChatPresets(self.c.data,language)
        self.assertEqual(set(DEFAULTS_EN),{key for key,_,_ in DEFAULTS})
        for item in presets.get()['items']:
            self.assertEqual((item['title'],item['prompt']),DEFAULTS_EN[item['id']])
            self.assertFalse(any('\u3400'<=c<='\u9fff' for c in item['title']+item['prompt']))
        self.assertEqual(presets.file.read_bytes(),disk)
        item=presets.get()['items'][0]
        self.assertFalse(item['featured'])
        # A second tab can switch language while the English editor is open.
        language.save({'language':'zh-CN'})
        self.change(presets,action='save',**item)
        self.assertEqual(presets.get()['items'][0]['title'],DEFAULTS[0][1])
        language.save({'language':'en'})
        self.assertEqual(ChatPresets(self.c.data,language).get()['items'][0],item)
        stored=json.loads(presets.file.read_text('utf-8'))['items'][0]
        self.assertEqual(stored['prompt'],DEFAULTS[0][2])
        self.change(presets,action='delete',id='people')
        language.save({'language':'zh-CN'})
        self.assertNotIn('people',[p['id'] for p in ChatPresets(self.c.data,language).get()['items']])

    def test_custom_and_edited_presets_remain_verbatim_in_both_languages(self):
        language=Language(self.c.data)
        presets=ChatPresets(self.c.data,language)
        # Same-title or exact-content user copies must still remain user data.
        self.change(presets,action='save',title=DEFAULTS[0][1],prompt=DEFAULTS[0][2])
        custom=presets.get()['items'][-1]
        self.change(presets,action='save',id='people',title=DEFAULTS[0][1],prompt='我修改过的提示词')
        language.save({'language':'en'})
        english=presets.get()['items']
        self.assertEqual(english[-1],custom)
        self.assertEqual(english[0]['prompt'],'我修改过的提示词')
        self.assertEqual(english[0]['title'],DEFAULTS[0][1])
        edited=dict(english[1],title='My clothing prompt')
        self.change(presets,action='save',**edited)
        disk=presets.file.read_bytes()
        language.save({'language':'zh-CN'})
        reopened=ChatPresets(self.c.data,language).get()['items']
        self.assertEqual(reopened[1]['title'],edited['title'])
        self.assertEqual(reopened[1]['prompt'],edited['prompt'])
        self.assertEqual(reopened[-1],custom)
        self.assertEqual(presets.file.read_bytes(),disk)

    def test_crud_featured_limit_and_empty_library_survive_restart(self):
        presets=ChatPresets(self.c.data)
        self.assertEqual(sum(p['featured'] for p in presets.get()['items']),5)
        self.change(presets,action='save',title='自定义人物',prompt='只推荐收藏中的人物。')
        item=presets.get()['items'][-1]
        self.assertFalse(item['featured'])
        before=presets.get()
        with self.assertRaisesRegex(ValueError,'最多显示 5'):
            self.change(presets,action='feature',id=item['id'],featured=True)
        self.assertEqual(presets.get(),before)
        self.change(presets,action='feature',id='people',featured=False)
        self.change(presets,action='feature',id=item['id'],featured=True)
        self.change(presets,action='save',id=item['id'],title='精选人物',prompt='推荐三个收藏人物。')
        reopened=ChatPresets(self.c.data)
        self.assertEqual(reopened.get(),presets.get())
        for p in reopened.get()['items']:
            self.change(reopened,action='delete',id=p['id'])
        self.assertEqual(ChatPresets(self.c.data).get()['items'],[])

    def test_invalid_or_stale_update_cannot_lose_existing_presets(self):
        presets=ChatPresets(self.c.data);old=presets.get()['revision']
        self.change(presets,action='save',title='新预设',prompt='推荐人物')
        before=presets.get();disk=presets.file.read_bytes()
        invalid=[{'action':'save','title':' ','prompt':'内容'},
                 {'action':'save','title':'标题','prompt':''},
                 {'action':'save','title':'a'*61,'prompt':'内容'},
                 {'action':'save','title':'标题','prompt':'a'*12001},
                 {'action':'feature','id':'people','featured':'false'},
                 {'action':'delete','id':'missing'}]
        for data in invalid:
            with self.assertRaises(ValueError):self.change(presets,**data)
        with self.assertRaisesRegex(ValueError,'其他页面'):
            presets.save({'revision':old,'action':'delete','id':'people'})
        self.assertEqual(presets.get(),before);self.assertEqual(presets.file.read_bytes(),disk)
        copy=presets.get();copy['items'].clear();self.assertTrue(presets.get()['items'])

    def test_failed_save_and_corrupt_file_preserve_previous_data(self):
        presets=ChatPresets(self.c.data);before=presets.get()
        with mock.patch('vampm.chat_presets.atomic_json',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):self.change(presets,action='delete',id='people')
        self.assertEqual(presets.get(),before)
        presets.file.write_text('{broken','utf-8');reopened=ChatPresets(self.c.data)
        self.assertTrue(reopened.get()['warning'])
        with self.assertRaises(ValueError):self.change(reopened,action='delete',id='people')
        self.assertEqual(presets.file.read_text('utf-8'),'{broken')

    def test_authenticated_crud_works_during_chat_and_never_changes_ai_settings(self):
        for index in range(2):
            app=Application(self.game,self.home);settings=app.ai.settings()
            server=serve(app);thread=threading.Thread(target=server.serve_forever);thread.start()
            url=f'http://127.0.0.1:{server.server_port}/api/chat-presets'
            headers={'X-Session-Token':app.token,'Content-Type':'application/json'}
            try:
                if index==0:
                    body=json.dumps({'action':'save','revision':0,'title':'新的推荐','prompt':'推荐三个发型。'}).encode()
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(urllib.request.Request(url,data=body))
                    with mock.patch.object(app.agent,'busy',return_value=True),mock.patch.object(app.jobs,'busy',return_value=True):
                        with urllib.request.urlopen(urllib.request.Request(url,data=body,headers=headers)) as r:
                            self.assertEqual(json.load(r)['items'][-1]['title'],'新的推荐')
                with urllib.request.urlopen(urllib.request.Request(url,headers=headers)) as r:
                    self.assertEqual(json.load(r)['items'][-1]['prompt'],'推荐三个发型。')
                self.assertEqual(app.ai.settings(),settings)
                with self.assertRaises(ValueError):app.post('/api/chat-presets/draft',{'title':'','prompt':''})
                self.assertEqual(app.jobs.all(),[])
            finally:
                server.shutdown();server.server_close();thread.join()
