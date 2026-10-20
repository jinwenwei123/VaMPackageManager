import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from vampm.catalog import Catalog,inspect_var
from vampm.covers import Covers,cover_candidates
from vampm.metadata import load_metadata,content_evidence,representative_names
from vampm.providers import AI,Downloads,approved_url
from vampm.sources import Sources,Links
from vampm.util import Job,atomic_json


class Response(io.BytesIO):
    def __init__(self,data,**headers):
        super().__init__(data);self.headers={'Content-Length':str(len(data)),**headers}


class Upgrade(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.game=self.root/'game';self.game.mkdir();self.c=Catalog(self.game,self.root/'tool')
        self.closed=mock.patch('vampm.providers.require_game_closed');self.closed.start()
    def tearDown(self):self.closed.stop();self.tmp.cleanup()
    def put(self,pkg='A.Scene.1',**kw):
        p=self.c.roots['active']/(pkg+'.var');p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(fixtures.var_bytes(pkg,**kw));return p
    def scan(self):return self.c.scan(Job('scan'))
    def item(self,pkg='B.Asset.1',**kw):
        return dict(filename=pkg+'.var',url='https://hub.virtamate.com/download',refs=[pkg],bytes=0,**kw)

    def test_cover_prioritizes_scene_then_appearance_not_skin_maps(self):
        names=['Custom/Textures/Skin/cover.png','Custom/Atom/Person/Appearance/Luna.jpg',
               'Custom/Atom/Person/Appearance/Luna.vap','Saves/scene/Test.jpg','Saves/scene/Test.json']
        infos=[zipfile.ZipInfo(n) for n in names]
        for i in infos:i.file_size=30
        result=[i.filename for i in cover_candidates(infos)]
        self.assertEqual(result[0],'Saves/scene/Test.jpg')
        self.assertNotIn(names[0],result)

    def test_cover_cache_handles_absence_and_changed_file(self):
        p=self.put();self.scan();covers=Covers(self.c)
        self.assertIsNone(covers.get('a.scene.1'))
        with mock.patch('vampm.covers.zipfile.ZipFile',side_effect=AssertionError('cache miss')):
            self.assertIsNone(covers.get('a.scene.1'))
        p.write_bytes(fixtures.var_bytes('A.Scene.1',files={'Saves/scene/Test.json':'{}','Saves/scene/Test.png':b'\x89PNG\r\n\x1a\n'+b'fixture'}))
        self.assertEqual(covers.get('a.scene.1')[1],'image/png')
        self.assertEqual(len(covers.cache),2)

    def test_metadata_recovers_prose_and_eof_without_mutation(self):
        raw=b'{\n "creatorName":"A",\n "packageName":"Scene",\n "description":"Use "dance" here",\n "dependencies":{}\n'
        value,warnings=load_metadata(raw)
        self.assertEqual(value['description'],'Use "dance" here');self.assertEqual(len(warnings),2)
        self.assertEqual(value['creatorName'],'A')
        for bad in ['{"creatorName":"A"evil","packageName":"Scene"}', '{"description":"unfinished', '{"dependencies":{"Bad.Asset.1":']:
            with self.assertRaises((ValueError,json.JSONDecodeError)):load_metadata(bad)

    def test_scene_evidence_is_specific_balanced_and_bounded(self):
        files={f'Custom/Textures/Skin/{i}.jpg':'x' for i in range(70)}
        files.update({'Saves/scene/BeachDance.json':json.dumps({'atoms':[{'type':'Person','id':'Person'},
                       {'type':'CustomUnityAsset','id':'OceanPalmTrees'},{'type':'AudioSource','id':'DanceMusic'}]}),
                      'Custom/Hair/Female/Bob.vam':'{}'})
        p=self.put(files=files,description='Beach dancing with a bob hairstyle.')
        hints=content_evidence(p)
        self.assertIn('Saves/scene/BeachDance.json',hints['samples'])
        self.assertIn('Custom/Hair/Female/Bob.vam',hints['samples'])
        self.assertEqual(hints['scene_hints'][0]['named_objects'],['OceanPalmTrees','DanceMusic'])
        self.assertLessEqual(len(hints['samples']),48)

    def test_models_are_independent_and_existing_chat_choice_preserved(self):
        ai=AI(self.c)
        atomic_json(ai.file,{'provider':'codex','codex_model':'gpt-6.1-sol'})
        self.assertEqual(ai.settings()['codex_summary_model'],'gpt-6-luna')
        ai.save({'codex_summary_model':'gpt-6-luna'})
        self.assertEqual(ai.settings()['codex_model'],'gpt-6.1-sol')

    def test_single_reanalysis_keeps_other_manual_summaries_and_uses_content_evidence(self):
        self.put('A.AI.1',description='A beach environment with palms.');self.put('A.Manual.1');self.scan()
        self.c.label('a.ai.1',summary='旧简介',source='ai')
        self.c.label('a.manual.1',summary='我的手动简介',source='manual')
        ai=AI(self.c)
        def classify(items,job):
            self.assertEqual([i['key'] for i in items],['a.ai.1']);self.assertIn('scene_hints',items[0])
            return {'a.ai.1':{'category':'环境道具','summary':'海滩布景资源，包含棕榈树和海景环境。'}}
        with mock.patch.object(ai,'classify_batch',side_effect=classify):
            r=ai.generate(['a.ai.1'],Job('ai'),overwrite=True)
        self.assertEqual(r,{'updated':1,'skipped':0})
        self.assertEqual(self.c.packages()['a.manual.1']['summary'],'我的手动简介')

    def test_summary_length_is_advisory_and_storage_preserves_full_text(self):
        self.put('A.Look.1');self.scan();self.c.packages()  # Exercise both the live cache and persisted label.
        descriptions=['人物','人物外观包。女性形象。外观偏年轻。银白长发。五官柔和。清新幻想风。',
                      ('这是一段用于验证完整保存的中文人物外观说明。'*100)+'结尾内容也要保留。']
        ai=AI(self.c)
        for description in descriptions:
            result=AI.validate({'items':[{'key':'a.look.1','category':'人物','summary':description}]},{'a.look.1'})
            with mock.patch.object(ai,'classify_batch',return_value=result):
                ai.generate(['a.look.1'],Job('summary'),overwrite=True)
            self.assertEqual(self.c.detail('a.look.1')['summary'],description)
            self.assertEqual(Catalog(self.game,self.root/'tool').detail('a.look.1')['summary'],description)
        for invalid in ['', '   ',None,42,'English only']:
            with self.assertRaises(ValueError):
                AI.validate({'items':[{'key':'a.look.1','category':'人物','summary':invalid}]},{'a.look.1'})

    def test_stale_publish_size_can_pass_full_integrity_checks(self):
        data=fixtures.var_bytes('B.Asset.1');d=Downloads(self.c)
        item=self.item();item['bytes']=len(data)+42
        with mock.patch('vampm.providers.trusted_open',return_value=Response(data)):
            r=d.download_one(item,Job('test'))
        self.assertEqual(r['status'],'installed');self.assertTrue(r['warnings'])
        self.assertEqual(self.c.stats()['files'],1)

    def test_incomplete_download_still_rejected(self):
        d=Downloads(self.c);data=fixtures.var_bytes('B.Asset.1')
        for item,headers in [(self.item(),{'Content-Length':str(len(data)+1)})]:
            with mock.patch('vampm.providers.trusted_open',return_value=Response(data,**headers)):
                with self.assertRaises(ValueError):d.download_one(item,Job('bad'))
        self.assertEqual(self.c.stats()['files'],0)

    def test_some_refs_already_resolved_does_not_skip_missing_exact(self):
        self.put('B.Asset.2');self.scan();d=Downloads(self.c)
        item=self.item();item['refs']=['B.Asset.latest','B.Asset.1']
        with mock.patch('vampm.providers.trusted_open',return_value=Response(fixtures.var_bytes('B.Asset.1'))):
            self.assertEqual(d.download_one(item,Job('test'))['status'],'installed')

    def test_download_refresh_prunes_ready_and_explains_transitive_dependencies(self):
        self.put(deps=['B.Asset.1','C.Broken.1']);self.scan();d=Downloads(self.c)
        atomic_json(d.report,{'created':'test','ready':[self.item(),self.item('C.Broken.1')],'unresolved':[], 'missing_count':2})
        data=fixtures.var_bytes('B.Asset.1',deps=['D.New.1'])
        with mock.patch('vampm.providers.trusted_open',side_effect=[Response(data),ValueError('offline')]):
            log=d.install(Job('install'))
        self.assertEqual((log['installed'],log['failed'],log['resolved'],log['new_missing'],log['remaining_missing']),(1,1,1,1,2))
        current=Downloads(self.c).get()
        self.assertEqual([x['filename'] for x in current['ready']],['C.Broken.1.var'])
        self.assertEqual([x['ref'] for x in current['unresolved']],['D.New.1'])
        self.assertEqual(current['last_install']['failed'],1)

    def test_github_author_discovery_and_configured_public_downloads(self):
        p=self.put('Author.Plugin.2');
        with zipfile.ZipFile(p,'w') as z:z.writestr('meta.json',json.dumps({'promotionalLink':'https://github.com/Author/plugin','dependencies':{}}))
        self.scan();sources=Sources(self.c)
        self.assertIn('Author/plugin',sources.repositories({})['author.plugin'])
        saved=sources.save({'sites':[{'name':'作者官网','prefix':'Author.*','url':'https://author.example/releases',
                           'download_hosts':'cdn.author.example'}]})
        html=b'<a href="https://cdn.author.example/files/Author.Plugin.1.var">Download</a><a href="https://evil.example/Author.Plugin.1.var">Mirror</a>'
        errors=[]
        with mock.patch('vampm.providers.trusted_open',return_value=Response(html)):
            hits=sources.external([{'ref':'Author.Plugin.1'},{'ref':'Author.Plugin.3'}],Job('sources'),errors)
        self.assertFalse(errors);self.assertEqual(len(hits),1);self.assertEqual(hits[0]['refs'],['Author.Plugin.1'])
        self.assertFalse(approved_url('https://cdn.author.example.evil.test/x',sources.hosts(saved['sites'][0]['id'])))
        sources.save({'sites':[]});self.assertEqual(sources.hosts(saved['sites'][0]['id']),[])
