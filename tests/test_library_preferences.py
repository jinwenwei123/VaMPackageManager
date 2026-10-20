import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import test_manager as fixtures
from vampm.library_preferences import LibraryPreferences
from vampm.server import Application, serve
from vampm.util import atomic_json


class BrowsingPreferences(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_only_presentation_persists_and_invalid_update_is_atomic(self):
        prefs=LibraryPreferences(self.c.data)
        self.assertEqual(prefs.get(),{'view':'list','sort':'name'})
        prefs.save({'view':'grid','sort':'new','state':'active','page':7,'q':'private search'})
        prefs.save({'sort':'size'})
        with self.assertRaises(ValueError):prefs.save({'view':'list','sort':'invalid'})
        self.assertEqual(LibraryPreferences(self.c.data).get(),{'view':'grid','sort':'size'})
        self.assertEqual(json.loads(prefs.file.read_text()),{'view':'grid','sort':'size'})
        atomic_json(prefs.file,{'view':'invalid','sort':{},'state':'active'})
        self.assertEqual(LibraryPreferences(self.c.data).get(),{'view':'list','sort':'name'})
        prefs.file.write_text('{broken')
        self.assertEqual(LibraryPreferences(self.c.data).get(),{'view':'list','sort':'name'})

    def test_authenticated_preferences_survive_new_server_and_busy_jobs(self):
        for index in range(2):
            app=Application(self.game,self.home)
            before=app.ai.settings()
            server=serve(app);thread=threading.Thread(target=server.serve_forever);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            headers={'X-Session-Token':app.token,'Content-Type':'application/json'}
            try:
                if index==0:
                    body=json.dumps({'view':'grid','sort':'size'}).encode()
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(urllib.request.Request(base+'/api/library-preferences',data=body))
                    with mock.patch.object(app.jobs,'busy',return_value=True),mock.patch.object(app.agent,'busy',return_value=True):
                        with urllib.request.urlopen(urllib.request.Request(base+'/api/library-preferences',data=body,headers=headers)) as r:
                            self.assertEqual(json.load(r),{'view':'grid','sort':'size'})
                with urllib.request.urlopen(urllib.request.Request(base+'/api/status',headers=headers)) as r:
                    self.assertEqual(json.load(r)['library_preferences'],{'view':'grid','sort':'size'})
                self.assertEqual(app.ai.settings(),before)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_missing_page_stays_valid_after_filter_or_install(self):
        app=Application(self.game,self.home)
        rows=[{'ref':f'Author.Asset{i}.1','users':['Scene'],'count':1} for i in range(81)]
        with mock.patch.object(app.c,'missing',return_value=rows):
            self.assertEqual(app.get('/api/missing',{'page':99})['page'],2)
            result=app.get('/api/missing',{'page':2,'q':'Asset80'})
            self.assertEqual(result['page'],1)
            self.assertEqual(result['items'],[rows[-1]])
        with mock.patch.object(app.c,'missing',return_value=[]):
            self.assertEqual(app.get('/api/missing',{'page':2}),{'total':0,'items':[],'page':1})
