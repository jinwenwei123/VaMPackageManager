import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.appearance import Appearance
from vampm.catalog import Catalog
from vampm.providers import Downloads
from vampm.server import Application, serve
from vampm.util import Job, atomic_json


class DownloadWarnings(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def item(self):
        return {'filename':'B.Asset.1.var','url':'https://hub.virtamate.com/download',
                'refs':['B.Asset.1'],'bytes':0}

    def test_identity_response_name_size_and_hash_are_retained_without_repacking(self):
        data=fixtures.var_bytes('Other.InternalName.1')
        item=dict(self.item(),bytes=len(data)+7,digest='sha256:'+'0'*64)
        response=Response(data,**{'Content-Disposition':"attachment; filename*=UTF-8''Other.Release.2.var"})
        with mock.patch('vampm.providers.trusted_open',return_value=response):
            result=Downloads(self.c).download_one(item,Job('download'))
        self.assertEqual(result['status'],'installed')
        target=self.c.roots['active']/'包管家补充/B.Asset.1.var'
        self.assertEqual(target.read_bytes(),data)
        self.assertEqual(len(result['warnings']),5)
        text=' '.join(result['warnings'])
        for phrase in ('Other.Release.2','Other','InternalName','SHA-256','体积'):
            self.assertIn(phrase,text)
        self.scan(True)
        fresh=Catalog(self.game,self.home)
        p=fresh.packages()['b.asset.1']
        self.assertFalse(p['error']);self.assertEqual(len(p['warnings']),5)
        self.assertEqual(fresh.list({'state':'warnings'})['total'],1)

    def test_unreadable_metadata_is_saved_flagged_and_not_redownloaded_or_resolved(self):
        self.put(deps=['B.Asset.1']);self.scan()
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('meta.json','{"creatorName":broken}')
            z.writestr('Saves/scene/Test.json','{}')
        d=Downloads(self.c)
        atomic_json(d.report,{'ready':[self.item()],'unresolved':[],'missing_count':1})
        with mock.patch('vampm.providers.trusted_open',return_value=Response(data.getvalue())):
            result=d.install(Job('download'))
        self.assertEqual((result['installed'],result['failed'],result['unreadable'],result['warned']),(1,0,1,1))
        self.assertEqual(result['resolved'],0)
        self.assertEqual(result['remaining_missing'],1)
        p=self.c.packages()['b.asset.1']
        self.assertTrue(p['error']);self.assertTrue(p['warnings'])
        self.assertEqual(d.get()['ready'],[])
        self.assertIn('已下载保留',d.get()['unresolved'][0]['reason'])
        with mock.patch('vampm.providers.trusted_open',side_effect=AssertionError('redownload')):
            self.assertEqual(d.install(Job('again'))['items'],[])
        self.assertEqual((self.c.roots['active']/'包管家补充/B.Asset.1.var').read_bytes(),data.getvalue())

    def test_missing_metadata_retained_and_existing_file_never_overwritten(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('Custom/Hair/Test.vam','{}')
        d=Downloads(self.c)
        with mock.patch('vampm.providers.trusted_open',return_value=Response(data.getvalue())):
            r=d.download_one(self.item(),Job('download'))
        self.assertTrue(r['package_error']);self.assertTrue(r['warnings'])
        with mock.patch('vampm.providers.trusted_open',side_effect=AssertionError('network')):
            with self.assertRaisesRegex(ValueError,'已存在'):d.download_one(self.item(),Job('again'))

    def test_unrelated_local_broken_copy_does_not_block_new_download(self):
        old=self.put('B.Asset.1',folder='older',data=b'broken local copy')
        self.put(deps=['B.Asset.1']);self.scan()
        d=Downloads(self.c)
        atomic_json(d.report,{'ready':[self.item()],'unresolved':[],'missing_count':1})
        self.assertEqual(len(d.get()['ready']),1)
        with mock.patch('vampm.providers.trusted_open',return_value=Response(fixtures.var_bytes('B.Asset.1'))):
            self.assertEqual(d.install(Job('replacement'))['installed'],1)
        self.assertEqual(old.read_bytes(),b'broken local copy')
        self.assertTrue(self.c.resolver()('B.Asset.1'))

    def test_crc_corruption_still_rejected_and_temp_removed(self):
        data=bytearray(fixtures.var_bytes('B.Asset.1'))
        # Corrupt the first local file's CRC in the central directory, leaving ZIP readable.
        pos=data.index(b'PK\x01\x02')+16
        data[pos:pos+4]=b'\x00'*4
        with mock.patch('vampm.providers.trusted_open',return_value=Response(bytes(data))):
            with self.assertRaises(zipfile.BadZipFile):Downloads(self.c).download_one(self.item(),Job('bad'))
        self.assertEqual(list(self.c.storage.path('downloads').glob('*')),[])
        self.assertFalse(list(self.c.roots['active'].rglob('*.var')))


class AppearanceSettings(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown

    def test_persistence_validation_and_partial_updates(self):
        a=Appearance(self.c.data)
        self.assertEqual(a.get(),{'palette':'forest','mode':'light'})
        a.save({'palette':'violet','mode':'dark'})
        self.assertEqual(Appearance(self.c.data).get(),{'palette':'violet','mode':'dark'})
        a.save({'mode':'system'})
        with self.assertRaises(ValueError):a.save({'palette':'ocean','mode':'bad'})
        self.assertEqual(Appearance(self.c.data).get(),{'palette':'violet','mode':'system'})
        atomic_json(a.file,{'palette':'injected();','mode':{},'secret':'not exposed'})
        self.assertEqual(Appearance(self.c.data).get(),{'palette':'forest','mode':'light'})

    def test_can_change_theme_during_jobs_without_touching_ai_settings(self):
        app=Application(self.game,self.home)
        before=app.ai.settings()
        with mock.patch.object(app.jobs,'busy',return_value=True),mock.patch.object(app.agent,'busy',return_value=True):
            self.assertEqual(app.post('/api/appearance',{'palette':'rose','mode':'dark'}),{'palette':'rose','mode':'dark'})
        self.assertEqual(app.ai.settings(),before)
        self.assertEqual(app.get('/api/status',{})['appearance'],{'palette':'rose','mode':'dark'})

    def test_bootstrap_survives_new_port_and_post_requires_session(self):
        ports=[]
        for index in range(2):
            app=Application(self.game,self.home)
            server=serve(app);thread=threading.Thread(target=server.serve_forever);thread.start()
            port=server.server_port;ports.append(port)
            base=f'http://127.0.0.1:{port}'
            try:
                if index==0:
                    data=json.dumps({'palette':'amber','mode':'dark'}).encode()
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(urllib.request.Request(base+'/api/appearance',data=data,headers={'Content-Type':'application/json'}))
                    with urllib.request.urlopen(urllib.request.Request(base+'/api/appearance',data=data,
                        headers={'Content-Type':'application/json','X-Session-Token':app.token})) as r:
                        self.assertEqual(json.load(r)['palette'],'amber')
                with urllib.request.urlopen(base+'/appearance-config.js') as r:
                    body=r.read().decode()
                    self.assertIn('"palette": "amber"',body);self.assertIn('"mode": "dark"',body)
                    self.assertNotIn(app.token,body);self.assertNotIn(str(self.game),body)
                    self.assertEqual(r.headers['Cache-Control'],'no-store')
            finally:
                server.shutdown();server.server_close();thread.join()
        self.assertNotEqual(*ports)
