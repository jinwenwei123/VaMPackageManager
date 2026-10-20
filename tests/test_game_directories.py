import subprocess
import threading
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.game_directories import GameDirectories, WorkspaceChanged
from vampm.server import Application
from vampm.util import Job, atomic_json, sha256


class DirectoryTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan

    def app(self):
        app = Application(self.game, self.home)
        self.addCleanup(lambda: app.active.ai.close())
        self.addCleanup(lambda: app.active.agent.close())
        return app

    def second(self, app):
        game = self.root / 'second'
        (game / 'AddonPackages').mkdir(parents=True)
        file = game / 'AddonPackages' / 'Shared.Look.1.var'
        file.write_bytes(fixtures.var_bytes('Shared.Look.1'))
        result = app.post('/api/game-directories/add', {'path': str(game), 'name': '另一版本'})
        return result['items'][-1]['id'], game, file

    def directory_alias(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            if getattr(error, 'winerror', None) != 1314: raise
            # NTFS junctions test the same alias protection without symlink privileges.
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],check=True,capture_output=True)

    def test_original_data_and_identical_keys_survive_switches_and_restart(self):
        file = self.put('Shared.Look.1'); digest = sha256(file); self.scan()
        app = self.app()
        app.c.label('shared.look.1', summary='原版简介', tags=['原版标签'], favorite=True)
        app.ops.save_profile('原版方案', ['shared.look.1'])
        app.agent._entry('user', '原版对话'); app.agent._save()
        app.post('/api/home-layout', {'cards': ['directories', 'favorites']})
        app.post('/api/appearance', {'palette': 'violet', 'mode': 'dark'})
        app.ai.save({'provider':'compatible','base_url':'https://example.invalid/v1','api_model':'original-model'})
        app.ai.usage.record(app.ai.settings(),'original-model','chat',{'usage':{'prompt_tokens':128,'completion_tokens':16}})
        app.library_preferences.save({'view':'grid','sort':'new'})
        app.chat_presets.save({'revision':0,'action':'save','title':'原版预设','prompt':'查看原版收藏'})
        atomic_json(app.c.data/'summary-queue.json', {'status':'paused','scope':'all','mode':'missing','target':'both',
                    'run_id':'original-run','keys':['shared.look.1'],'current':0,'total':1,'updated':0})
        atomic_json(app.c.data/'operations'/'old'/'manifest.json', {'id':'old','kind':'organize','created':'2026-01-01','status':'done','count':1})
        (app.c.data/'disabled').mkdir(exist_ok=True)
        (app.c.data/'disabled'/'legacy-sentinel').write_bytes(b'legacy vault stays here')
        (app.c.data/'quarantine').mkdir(exist_ok=True)
        (app.c.data/'quarantine'/'sentinel').write_bytes(b'isolated resources stay here')
        preserved={p.relative_to(app.c.data):p.read_bytes() for p in app.c.data.rglob('*') if p.is_file() and p.suffix=='.json' and p.name!='game-directories.json'}
        ident, second, new_file = self.second(app)
        info=app.post('/api/game-directories/switch', {'id':ident})
        self.assertEqual(info['id'],ident)
        self.assertEqual(app.active.c.home,self.home)
        self.assertEqual(app.active.c.data,self.home/'data'/'workspaces'/ident)
        self.assertEqual(app.get('/api/packages',{})['total'],0)  # Switching never scans.
        self.assertEqual(app.active.appearance.get()['palette'],'violet')
        self.assertEqual(app.active.ai.settings()['api_model'],'original-model')
        self.assertEqual(app.active.ai.usage.public(),[])
        self.assertEqual(app.active.library_preferences.get()['view'],'list')
        self.assertFalse(any(p['title']=='原版预设' for p in app.active.chat_presets.get()['items']))
        app.active.ai.save({'api_model':'other-model'})
        app.active.c.scan(Job('fixture scan'))
        app.active.c.label('shared.look.1', summary='另一版本简介', tags=['另一标签'])
        app.active.ops.save_profile('另一方案',['shared.look.1'])
        app.active.agent._entry('user','另一对话');app.active.agent._save()
        app.post('/api/home-layout',{'cards':['recent','directories']})
        app.post('/api/appearance',{'palette':'ocean','mode':'light'})
        self.assertEqual(app.get('/api/history',{}),[])
        self.assertEqual(sha256(new_file),digest)
        for path, content in preserved.items(): self.assertEqual((self.home/'data'/path).read_bytes(),content,str(path))
        app.post('/api/game-directories/switch', {'id':'original'})
        package=app.get('/api/package',{'key':'shared.look.1'})
        self.assertEqual(package['summary'],'原版简介');self.assertEqual(package['tags'],['原版标签']);self.assertTrue(package['favorite'])
        self.assertTrue(any(p['name']=='原版方案' for p in app.get('/api/profiles',{})))
        self.assertFalse(any(p['name']=='另一方案' for p in app.get('/api/profiles',{})))
        self.assertEqual(app.active.agent.data['messages'][0]['text'],'原版对话')
        self.assertEqual(app.active.ai.settings()['api_model'],'original-model')
        self.assertEqual(app.active.ai.usage.public()[0]['prompt_tokens'],128)
        self.assertEqual(app.active.library_preferences.get()['view'],'grid')
        self.assertTrue(any(p['title']=='原版预设' for p in app.active.chat_presets.get()['items']))
        self.assertEqual(app.active.summaries.public()['status'],'paused')
        self.assertEqual(app.active.home_layout.get()['cards'],['directories','favorites'])
        self.assertEqual(app.get('/api/history',{})[0]['id'],'old')
        self.assertEqual(sha256(file),digest)
        self.assertTrue((self.home/'data'/'disabled'/'legacy-sentinel').exists())
        self.assertTrue((self.home/'data'/'quarantine'/'sentinel').exists())
        app.post('/api/game-directories/switch',{'id':ident})
        app.active.agent.close();app.active.ai.close()
        restored=self.app()
        self.assertEqual(restored.active.c.game,second)
        self.assertEqual(restored.get('/api/package',{'key':'shared.look.1'})['summary'],'另一版本简介')
        self.assertEqual(restored.active.home_layout.get()['cards'],['recent','directories'])
        self.assertEqual(restored.active.agent.data['messages'][0]['text'],'另一对话')
        self.assertEqual(restored.active.ai.settings()['api_model'],'other-model')

    def test_stale_writes_are_rejected_even_after_switching_back(self):
        self.put('Shared.Look.1');self.scan();app=self.app();ident,_,_=self.second(app)
        old=app.workspace_epoch
        app.post('/api/game-directories/switch',{'id':ident},scope=old)
        for scope in (old,''):
            with self.assertRaises(WorkspaceChanged):app.post('/api/label',{'key':'shared.look.1','summary':'wrong'},scope=scope)
        with self.assertRaises(WorkspaceChanged):app.get('/api/profiles',{},scope=old)
        app.post('/api/game-directories/switch',{'id':'original'},scope=app.workspace_epoch)
        with self.assertRaises(WorkspaceChanged):app.post('/api/label',{'key':'shared.look.1','summary':'wrong'},scope=old)
        self.assertNotEqual(app.get('/api/package',{'key':'shared.look.1'})['summary'],'wrong')

    def test_failed_or_busy_switch_keeps_current_state_usable(self):
        app=self.app();ident,_,_=self.second(app);old=app.workspace_epoch
        app.jobs.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'任务'):app.post('/api/game-directories/switch',{'id':ident})
        finally:app.jobs.lock.release()
        app.agent.turn_lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'任务'):app.post('/api/game-directories/switch',{'id':ident})
        finally:app.agent.turn_lock.release()
        with mock.patch.object(app.ops,'check_pending',side_effect=ValueError('未完成的文件操作')):
            with self.assertRaisesRegex(ValueError,'未完成'):app.post('/api/game-directories/switch',{'id':ident})
        with mock.patch.object(app.directories,'activate',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):app.post('/api/game-directories/switch',{'id':ident})
        self.assertIs(app.active,app);self.assertFalse(app.ai.closed)
        self.assertEqual(app.workspace_epoch,old);self.assertEqual(app.directories.value['current'],'original')
        with mock.patch('vampm.server.Catalog',side_effect=ValueError('broken target database')):
            with self.assertRaises(ValueError):app.post('/api/game-directories/switch',{'id':ident})
        self.assertIs(app.active,app);self.assertFalse(app.ai.closed)

    def test_unavailable_saved_game_does_not_recreate_paths_or_drop_data(self):
        app=self.app();ident,game,_=self.second(app)
        app.post('/api/game-directories/switch',{'id':ident});app.active.c.scan(Job('fixture scan'))
        app.active.c.label('shared.look.1',summary='失联前的简介')
        moved=self.root/'unplugged';game.rename(moved)
        restored=self.app()
        self.assertFalse(restored.get('/api/status',{})['workspace']['available'])
        self.assertFalse(game.exists())
        self.assertEqual(restored.get('/api/package',{'key':'shared.look.1'})['summary'],'失联前的简介')
        with self.assertRaisesRegex(ValueError,'不可用'):restored.post('/api/scan',{})
        restored.post('/api/game-directories/switch',{'id':'original'})
        moved.rename(game);restored.post('/api/game-directories/switch',{'id':ident})
        self.assertEqual(restored.get('/api/package',{'key':'shared.look.1'})['summary'],'失联前的简介')

    def test_late_read_keeps_its_original_library(self):
        self.put('Shared.Look.1');self.scan();app=self.app();app.c.label('shared.look.1',summary='原目录')
        ident,_,_=self.second(app);entered,release=threading.Event(),threading.Event();results=[]
        original=app._get
        def slow(path,query):
            entered.set();release.wait(5);return original(path,query)
        with mock.patch.object(app,'_get',side_effect=slow):
            worker=threading.Thread(target=lambda:results.append(app.get('/api/package',{'key':'shared.look.1'},scope=app.workspace_epoch)))
            worker.start();self.assertTrue(entered.wait(5))
            app.post('/api/game-directories/switch',{'id':ident});release.set();worker.join(5)
        self.assertEqual(results[0]['summary'],'原目录');self.assertEqual(app.get('/api/packages',{})['total'],0)

    def test_registry_rejects_duplicate_invalid_and_unsafe_paths(self):
        app=self.app();ident,game,_=self.second(app)
        for path in (str(game),str(game/'AddonPackages'/'..'),'relative/path',str(self.root/'missing'),str(self.home)):
            with self.assertRaises((ValueError,OSError)):app.post('/api/game-directories/add',{'path':path})
        alias=self.root/'alias';self.directory_alias(alias,game)
        with self.assertRaises(ValueError):app.post('/api/game-directories/add',{'path':str(alias)})
        inside=self.game/'AddonPackages'/'nested';(inside/'AddonPackages').mkdir(parents=True)
        with self.assertRaises(ValueError):app.post('/api/game-directories/add',{'path':str(inside)})
        shared=self.root/'shared-library';shared.mkdir()
        self.directory_alias(shared/'AddonPackages',game/'AddonPackages')
        with self.assertRaisesRegex(ValueError,'重叠'):app.post('/api/game-directories/add',{'path':str(shared)})
        app.post('/api/game-directories/rename',{'id':ident,'name':'改名'})
        self.assertEqual(app.directories.entry(ident)['name'],'改名')
        self.assertEqual(app.directories.entry(ident)['path'],str(game))
        self.assertFalse(app.directories.location(ident).exists())
        before=app.directories.file.read_bytes();atomic_json(app.directories.file,{'version':1,'current':'../escape','items':[]})
        with self.assertRaises(ValueError):GameDirectories(self.home,self.game)
        app.directories.file.write_bytes(before)

    def test_layout_can_be_empty_reordered_and_restored_without_accepting_unknown_cards(self):
        app=self.app();default=app.home_layout.get()['cards']
        self.assertEqual(len(app.home_layout.get()['available']),16)
        app.post('/api/home-layout',{'cards':['recent','directories','library']})
        self.assertEqual(self.app().home_layout.get()['cards'],['recent','directories','library'])
        for value in (['recent','recent'],['unknown'],[{}],'library',None):
            with self.assertRaises(ValueError):app.post('/api/home-layout',{'cards':value})
        app.post('/api/home-layout',{'cards':[]});self.assertEqual(self.app().home_layout.get()['cards'],[])
        app.post('/api/home-layout',{'cards':default});self.assertEqual(app.home_layout.get()['cards'],default)
