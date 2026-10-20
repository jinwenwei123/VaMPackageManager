import io
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from vampm.catalog import Catalog
from vampm.operations import Operations
from vampm.server import Application
from vampm.agent_tools import invoke
from vampm.providers import Downloads
from vampm.util import Job


class CollectionTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def test_collection_directory_rename_preserves_sidecars_ids_and_profile_then_undoes(self):
        original=self.put(folder='自定义包/人物');self.scan();self.c.label('author.scene.1',category='人物')
        adopted=self.ops.adopt_favorites('自定义包',Job('adopt'))
        original=self.c.path(self.c.packages()['author.scene.1']['files'][0])
        self.ops.save_profile('人物',[],folder='自定义包/人物')
        sidecar=original.with_suffix('.jpg');sidecar.write_bytes(b'preview')
        empty=original.parent/'空目录';empty.mkdir()
        before={r['id']:(r['size'],r['mtime'],r['info'],r['sha']) for r in self.c.rows()}
        result=self.ops.rename_collection('收藏',Job('rename'))
        target=self.c.roots['vault']/'收藏/人物/Author.Scene.1.var'
        self.assertTrue(target.is_file());self.assertFalse(original.exists())
        self.assertEqual(target.with_suffix('.jpg').read_bytes(),b'preview')
        self.assertTrue((target.parent/'空目录').is_dir())
        self.assertEqual(self.c.favorite_folder(),'收藏')
        self.assertEqual(self.ops.profiles()[0]['source']['folder'],'收藏/人物')
        self.assertEqual(before,{r['id']:(r['size'],r['mtime'],r['info'],r['sha']) for r in self.c.rows()})
        self.assertEqual(self.c.stats()['favorites'],1)
        self.ops.undo(result['operation'],Job('undo'))
        self.assertTrue(original.exists());self.assertTrue(sidecar.exists());self.assertTrue(empty.exists())
        self.assertFalse((self.c.roots['vault']/'收藏').exists())
        self.assertEqual(self.ops.profiles()[0]['source']['folder'],'自定义包/人物')
        self.ops.undo(adopted['operation'],Job('undo adoption'))
        self.assertFalse(self.c.favorite_keys())

    def test_collection_rename_interruption_recovers_active_and_disabled_directories(self):
        first=self.put('A.One.1',folder='自定义包');self.put('B.Two.1',folder='自定义包');self.scan()
        self.ops.adopt_favorites('自定义包',Job('adopt'))
        plan=self.ops.plan('activate',['a.one.1'],Job('plan'));self.ops.apply(plan['id'],Job('activate'))
        first=self.c.path(self.c.packages()['a.one.1']['files'][0])
        from vampm.operations import move_no_replace
        count=[0]
        def fail(src,dst,job=None):
            count[0]+=1
            if count[0]==2:raise OSError('interrupted directory rename')
            move_no_replace(src,dst)
        with mock.patch('vampm.operations.move_no_replace',side_effect=fail):
            with self.assertRaises(OSError):self.ops.rename_collection('收藏',Job('rename'))
        operation=next(r for r in self.ops.history() if r['kind']=='rename_collection')
        self.assertEqual(operation['status'],'interrupted')
        self.ops.undo(operation['id'],Job('undo'))
        self.assertTrue(first.is_file())
        self.assertTrue((self.c.roots['vault']/'自定义包/场景/B.Two.1.var').is_file())
        self.assertEqual(self.c.favorite_folder(),'自定义包')
        self.assertEqual(self.c.stats()['active'],1)

    def test_collection_rename_rejects_collision_and_waits_for_later_operation_undo(self):
        self.put(folder='自定义包');self.scan();self.ops.adopt_favorites('自定义包',Job('adopt'))
        target=self.game/'AddonPackages/收藏';target.mkdir()
        with self.assertRaises(ValueError):self.ops.rename_collection('收藏',Job('collision'))
        target.rmdir()
        renamed=self.ops.rename_collection('收藏',Job('rename'))
        later=self.ops.favorite(['author.scene.1'],False,Job('remove'))
        with self.assertRaisesRegex(ValueError,'先恢复重命名之后'):self.ops.undo(renamed['operation'],Job('undo'))
        self.ops.undo(later['operation'],Job('undo favorite'))
        self.ops.undo(renamed['operation'],Job('undo rename'))
        self.assertEqual(self.c.favorite_folder(),'自定义包')

    def test_new_vault_uses_game_sibling_and_activation_restores_without_changing_files(self):
        self.assertEqual(self.c.roots['vault'],self.game/'.DisabledPackages')
        self.put('A.One.1');other=self.put('B.Two.1');self.scan()
        before=other.read_bytes()
        plan=self.ops.plan('activate',['a.one.1'],Job('plan'));self.ops.apply(plan['id'],Job('activate'))
        self.assertFalse(other.exists());self.assertEqual((self.game/'.DisabledPackages/场景/B.Two.1.var').read_bytes(),before)
        fresh=Catalog(self.game,self.home)
        self.assertEqual(fresh.roots['vault'],self.game/'.DisabledPackages')
        plan=self.ops.plan('restore_all',[],Job('restore plan'));self.ops.apply(plan['id'],Job('restore'))
        self.assertEqual((self.c.roots['active']/'场景/B.Two.1.var').read_bytes(),before)

    def test_existing_legacy_vault_is_retained_until_explicit_migration(self):
        home=self.root/'legacy-tool';legacy=home/'data/disabled';legacy.mkdir(parents=True)
        (legacy/'A.Legacy.1.var').write_bytes(fixtures.var_bytes('A.Legacy.1'))
        catalog=Catalog(self.game,home);catalog.scan(Job('scan'))
        self.assertEqual(catalog.roots['vault'],legacy)
        self.assertFalse(catalog.packages()['a.legacy.1']['active'])

    def test_favorite_round_trip_restores_path_labels_and_origins(self):
        original=self.put(folder='人物/原始');self.scan()
        self.c.label('author.scene.1',category='人物',summary='手动简介保留')
        first=self.ops.favorite(['author.scene.1'],True,Job('favorite'))
        target=self.c.roots['vault']/'收藏/人物/Author.Scene.1.var'
        self.assertTrue(target.exists());self.assertFalse(original.exists())
        self.assertTrue(self.c.packages()['author.scene.1']['favorite'])
        second=self.ops.favorite(['author.scene.1'],False,Job('unfavorite'))
        self.assertTrue((self.c.roots['vault']/'人物/Author.Scene.1.var').exists());self.assertFalse(target.exists())
        self.assertEqual(self.c.packages()['author.scene.1']['summary'],'手动简介保留')
        self.assertFalse(self.c.packages()['author.scene.1']['favorite'])
        self.ops.undo(second['operation'],Job('undo'))
        self.assertTrue(target.exists());self.assertTrue(self.c.packages()['author.scene.1']['favorite'])
        self.ops.undo(first['operation'],Job('undo'))
        self.assertTrue(original.exists());self.assertFalse(self.c.packages()['author.scene.1']['favorite'])
        self.assertEqual(self.c.favorite_origins(),{})

    def test_adopt_existing_folder_and_dynamic_favorites_include_dependencies(self):
        original=self.put('A.Scene.1',folder='自定义包/场景',deps=['B.Asset.1'])
        self.put('B.Asset.1');self.put('C.Other.1');self.scan()
        result=self.ops.adopt_favorites('自定义包',Job('adopt'),'基础收藏')
        self.assertEqual(result['count'],3);self.assertFalse(original.exists())
        self.assertEqual(self.c.stats()['active'],0)
        self.assertEqual(self.c.favorite_folder(),'自定义包')
        profile=result['profile'];self.assertEqual(profile['keys'],['a.scene.1'])
        self.assertEqual(profile['included_count'],2)
        self.put('A.New.1',folder='自定义包/人物',deps=['D.Missing.1']);self.scan()
        current=self.ops.profiles()[0]
        self.assertEqual(len(current['keys']),2);self.assertEqual(current['missing_count'],1)
        self.assertEqual(self.c.stats()['favorites'],2)
        self.assertFalse(self.c.packages()['b.asset.1']['favorite'])

    def test_cancel_adopted_favorite_without_original_moves_to_nonfavorites(self):
        original=self.put(folder='自定义包/人物');self.scan();self.c.label('author.scene.1',category='人物')
        self.ops.adopt_favorites('自定义包',Job('adopt'))
        original=self.c.path(self.c.packages()['author.scene.1']['files'][0])
        operation=self.ops.favorite(['author.scene.1'],False,Job('remove'))
        self.assertTrue((self.c.roots['vault']/'人物/Author.Scene.1.var').exists())
        self.scan();self.assertFalse(self.c.packages()['author.scene.1']['favorite'])
        self.ops.undo(operation['operation'],Job('undo'));self.assertTrue(original.exists())

    def test_favorites_profile_tracks_stars_undo_restart_and_transitive_dependencies(self):
        self.put('A.Scene.1',deps=['B.Asset.1'])
        self.put('B.Asset.1',deps=['C.Hair.1']);self.put('C.Hair.1')
        self.put('D.NewFavorite.1');self.put('E.Unrelated.1');self.scan()
        self.ops.favorite(['a.scene.1'],True,Job('favorite'))
        profile=self.ops.save_profile('基础收藏',[],favorites=True)[0]
        static=self.ops.save_profile('固定选择',['d.newfavorite.1'])[-1]
        self.assertEqual(self.ops.profiles()[0]['included_count'],3)
        self.ops.favorite(['d.newfavorite.1'],True,Job('new favorite'))
        current=self.ops.profiles()[0]
        self.assertEqual(current['id'],profile['id'])
        self.assertEqual(current['keys'],['a.scene.1','d.newfavorite.1'])
        self.assertEqual(current['included_count'],4)
        self.assertEqual(self.ops.profiles()[1]['keys'],['d.newfavorite.1'])
        removed=self.ops.favorite(['a.scene.1'],False,Job('remove favorite'))
        self.assertEqual(self.ops.profiles()[0]['keys'],['d.newfavorite.1'])
        self.assertEqual(self.ops.profiles()[0]['included_count'],1)
        self.ops.undo(removed['operation'],Job('undo favorite'))
        fresh=Operations(Catalog(self.game,self.home))
        self.assertEqual(fresh.profiles()[0]['included_count'],4)
        plan=fresh.plan('activate',[],Job('favorites preview'),profile_id=profile['id'])
        self.assertEqual(plan['selected'],2);self.assertEqual(plan['included'],4)
        self.assertNotIn('profile_snapshot',plan)
        fresh.apply(plan['id'],Job('activate favorites'))
        self.assertEqual(fresh.c.stats()['active'],4)
        self.assertFalse(fresh.c.packages()['e.unrelated.1']['active'])
        self.assertFalse(fresh.c.packages()['c.hair.1']['favorite'])
        # Favorites are not implicitly added to unrelated fixed profiles.
        plan=fresh.plan('activate',[],Job('fixed preview'),profile_id=static['id'])
        fresh.apply(plan['id'],Job('activate fixed'))
        self.assertEqual(fresh.c.stats()['active'],1)
        self.assertFalse(fresh.c.packages()['a.scene.1']['active'])
        self.assertEqual(fresh.profiles()[0]['included_count'],4)
        self.assertNotIn('profile_snapshot',fresh.history()[0])

    def test_empty_favorites_profile_survives_clear_and_new_favorite(self):
        profile=self.ops.save_profile('基础收藏',[],favorites=True)[0]
        self.assertEqual(profile['keys'],[])
        with self.assertRaises(ValueError):self.ops.save_profile('空的固定选择',[])
        self.put('A.Scene.1',folder='收藏');self.put('B.New.1');self.scan()
        self.assertEqual(self.ops.profiles()[0]['keys'],['a.scene.1'])
        self.ops.favorite(['a.scene.1'],False,Job('clear'))
        self.assertEqual(self.ops.profiles()[0]['keys'],[])
        empty=self.ops.plan('activate',[],Job('empty'),profile_id=profile['id'])
        self.ops.apply(empty['id'],Job('apply empty'))
        self.assertEqual(self.c.stats()['active'],0)
        self.ops.favorite(['b.new.1'],True,Job('add again'))
        self.assertEqual(self.ops.profiles()[0]['keys'],['b.new.1'])
        self.assertEqual(self.ops.profiles()[0]['id'],profile['id'])

    def test_favorites_changed_after_preview_blocks_old_plan_without_moving(self):
        self.put('A.Scene.1');self.put('B.New.1');self.scan()
        self.c.label('a.scene.1',favorite=True)
        profile=self.ops.save_profile('收藏',[],favorites=True)[0]
        plan=self.ops.plan('activate',[],Job('preview'),profile_id=profile['id'])
        signature=self.c.signature()
        self.c.label('b.new.1',favorite=True)
        self.assertEqual(self.c.signature(),signature)
        with self.assertRaisesRegex(ValueError,'收藏内容已更新'):
            self.ops.apply(plan['id'],Job('stale'))
        self.assertEqual(self.c.stats()['active'],2);self.assertEqual(self.ops.history(),[])
        fresh=self.ops.plan('activate',[],Job('new preview'),profile_id=profile['id'])
        self.assertEqual(fresh['selected'],2)

    def test_deep_dependency_changes_invalidate_profile_preview_without_file_changes(self):
        self.put('A.Scene.1',folder='收藏',files={'Saves/scene/A.json':'"B.Hair.1:/x"'})
        self.put('B.Hair.1');self.put('C.Unrelated.1');self.scan()
        profile=self.ops.save_profile('收藏',[],favorites=True)[0]
        plan=self.ops.plan('activate',[],Job('preview'),profile_id=profile['id'])
        self.assertEqual(plan['included'],1)
        signature=self.c.signature();self.scan(True)
        self.assertEqual(self.c.signature(),signature)
        self.assertEqual(self.ops.profiles()[0]['included_count'],2)
        with self.assertRaisesRegex(ValueError,'重新预览'):
            self.ops.apply(plan['id'],Job('stale'))
        self.assertEqual(self.c.stats()['active'],3);self.assertEqual(self.ops.history(),[])

    def test_deleted_or_changed_profile_cannot_use_its_old_preview(self):
        self.put('A.Scene.1');self.put('B.Other.1');self.scan()
        profile=self.ops.save_profile('我的方案',['a.scene.1'])[0]
        plan=self.ops.plan('activate',[],Job('preview'),profile_id=profile['id'])
        self.ops.save_profile('我的方案',['b.other.1'],profile_id=profile['id'])
        with self.assertRaisesRegex(ValueError,'重新预览'):
            self.ops.apply(plan['id'],Job('changed'))
        plan=self.ops.plan('activate',[],Job('preview'),profile_id=profile['id'])
        self.ops.delete_profile(profile['id'])
        with self.assertRaisesRegex(ValueError,'重新预览'):
            self.ops.apply(plan['id'],Job('deleted'))
        self.assertEqual(self.c.stats()['active'],2);self.assertEqual(self.ops.history(),[])

    def test_adoption_undo_restores_root_and_flags_without_moving_existing_files(self):
        original=self.put(folder='自定义包');self.scan()
        result=self.ops.adopt_favorites('自定义包',Job('adopt'))
        self.ops.undo(result['operation'],Job('undo'));self.scan()
        self.assertEqual(self.c.favorite_folder(),'收藏')
        self.assertFalse(self.c.packages()['author.scene.1']['favorite']);self.assertTrue(original.exists())

    def test_folder_profile_is_boundary_exact_and_updates_after_new_files(self):
        self.put('A.Scene.1',folder='精选');self.put('A.NotSelected.1',folder='精选2');self.scan()
        profile=self.ops.save_profile('场景',[],folder='精选')[0]
        self.assertEqual(profile['keys'],['a.scene.1'])
        self.put('B.New.1',folder='精选/子目录');self.scan()
        plan=self.ops.plan('activate',[],Job('plan'),profile_id=profile['id'])
        self.assertEqual(plan['selected'],2)
        self.ops.apply(plan['id'],Job('apply'))
        self.assertEqual(self.c.stats()['active'],2)
        self.assertFalse(self.c.packages()['a.notselected.1']['active'])

    def test_favorite_preserves_disabled_state(self):
        self.put('A.Scene.1');self.put('B.Other.1');self.scan()
        plan=self.ops.plan('activate',['b.other.1'],Job('plan'));self.ops.apply(plan['id'],Job('apply'))
        self.ops.favorite(['a.scene.1'],True,Job('favorite'))
        p=self.c.packages()['a.scene.1'];self.assertFalse(p['active']);self.assertTrue(p['favorite'])
        self.assertEqual(p['files'][0]['area'],'vault')

    def test_partial_favorite_failure_restores_every_file_and_flag(self):
        original=self.put('A.One.1');second=self.put('B.Two.1');self.scan()
        plan=self.ops.plan('favorite',['a.one.1','b.two.1'],Job('plan'))
        from vampm.operations import move_no_replace
        count=[0]
        def fail(src,dst,job=None):
            count[0]+=1
            if count[0]==2:raise OSError('fixture interruption')
            move_no_replace(src,dst)
        with mock.patch('vampm.operations.move_no_replace',side_effect=fail):
            with self.assertRaises(OSError):self.ops.apply(plan['id'],Job('apply'))
        self.ops.undo(plan['id'],Job('undo'))
        self.assertTrue(original.exists());self.assertTrue(second.exists());self.assertFalse(self.c.favorite_keys())

    def test_favorite_collision_never_overwrites(self):
        original=self.put(folder='original');self.scan()
        target=self.game/'AddonPackages/收藏/收藏新增/original/Author.Scene.1.var'
        target.parent.mkdir(parents=True);target.write_bytes(b'keep me')
        original_bytes=original.read_bytes()
        result=self.ops.favorite(['author.scene.1'],True,Job('favorite'))
        files=self.c.packages()['author.scene.1']['files']
        self.assertEqual(len(files),2)
        self.assertCountEqual([self.c.path(r).read_bytes() for r in files],[original_bytes,b'keep me'])
        self.assertTrue(all(Path(r['rel']).name=='Author.Scene.1.var' for r in files))
        self.ops.undo(result['operation'],Job('undo'))
        self.assertEqual(original.read_bytes(),original_bytes);self.assertEqual(target.read_bytes(),b'keep me')

    def test_warning_survives_deep_scan_move_and_reload_without_breaking_dependency(self):
        data=fixtures.var_bytes('B.Asset.1')
        class Response(io.BytesIO):headers={'Content-Length':str(len(data))}
        item={'filename':'B.Asset.1.var','url':'https://hub.virtamate.com/test','refs':['B.Asset.1'],'bytes':len(data)+10}
        with mock.patch('vampm.providers.trusted_open',return_value=Response(data)):
            Downloads(self.c).download_one(item,Job('download'))
        self.scan(True);self.ops.favorite(['b.asset.1'],True,Job('favorite'))
        fresh=Catalog(self.game,self.home);p=fresh.packages()['b.asset.1']
        self.assertTrue(p['warnings']);self.assertFalse(p['error']);self.assertTrue(fresh.resolver()('B.Asset.1'))
        self.assertEqual(fresh.list({'state':'warnings'})['total'],1)
        path=fresh.path(p['files'][0]);path.write_bytes(fixtures.var_bytes('B.Asset.1',description='changed'))
        fresh.scan(Job('scan'));self.assertFalse(fresh.packages()['b.asset.1']['warnings'])

    def test_recommendation_cards_follow_answer_and_refresh_favorite_state(self):
        self.put();app=Application(self.game,self.home);app.c.scan(Job('scan'))
        class FakeCodex:
            def __init__(self,*args):self.thread_id=None;self.process=None
            def connect_thread(self,*args):self.thread_id='cards';return 'cards'
            def turn(self,text,cancel,emit,tool):
                row=tool('search_packages',{})['items'][0]
                tool('show_packages',{'keys':[row['key']],'title':'精选资源'})
                emit('message',{'id':'answer','text':'推荐这个场景。'})
            def close(self):pass
        with mock.patch.object(app.ai.models,'reasoning',return_value=None),mock.patch('vampm.agent.CodexChat',FakeCodex):
            app.agent.send('推荐',read_only=True);app.agent.worker.join(timeout=10)
        snapshot=app.agent.snapshot();cards=snapshot['messages'][-1]
        self.assertEqual(cards['role'],'recommendations');self.assertEqual(snapshot['messages'][-2]['role'],'assistant')
        app.ops.favorite(['author.scene.1'],True,Job('favorite'))
        update=app.agent.snapshot(snapshot['seq'],snapshot['id'])
        self.assertTrue(update['messages'][-1]['items'][0]['favorite'])
        with self.assertRaises(ValueError):app.agent.call('set_favorites',{'keys':['author.scene.1'],'favorite':False})
        with self.assertRaises(ValueError):invoke(app,'show_packages',{'keys':['made.up.1']})

    def test_agent_builds_complete_folder_profile_without_a_key_list(self):
        for i in range(53):self.put('A.Scene'+str(i)+'.1',folder='某个文件夹')
        app=Application(self.game,self.home);app.c.scan(Job('scan'))
        folders=invoke(app,'list_folders',{'q':'某个文件夹'})
        self.assertEqual(folders['items'][0]['count'],53)
        profile=next(p for p in invoke(app,'manage_profiles',{'action':'save','name':'全部场景','folder':'某个文件夹'}) if p['name']=='全部场景')
        self.assertEqual(profile['keys_total'],53);self.assertTrue(profile['keys_truncated'])
        plan=invoke(app,'plan_files',{'kind':'activate','profile_id':profile['id']})
        self.assertEqual(plan['selected'],53)

    def test_upgrade_keeps_visible_history_and_recent_context_then_resumes_new_tools(self):
        from vampm.agent import TOOL_SIGNATURE
        app=Application(self.game,self.home)
        cfg=app.ai.settings(True)
        app.agent.data.update(thread_id='old-thread',provider='codex',model=cfg['codex_model'])
        app.agent._entry('user','之前我喜欢花园。')
        app.agent._entry('assistant','已推荐花园包。')
        starts=[];prompts=[]
        class FakeCodex:
            def __init__(self,*args):self.thread_id=None;self.process=None
            def connect_thread(self,thread,*args):
                starts.append(thread);self.thread_id='upgraded-thread';return self.thread_id
            def turn(self,text,cancel,emit,tool):
                prompts.append(text);emit('message',{'id':'reply-'+str(len(prompts)),'text':'测试回复'})
            def close(self):pass
        with mock.patch.object(app.ai.models,'reasoning',return_value=None),mock.patch('vampm.agent.CodexChat',FakeCodex):
            app.agent.send('继续推荐');app.agent.worker.join(timeout=10)
            app.agent.send('再说说');app.agent.worker.join(timeout=10)
        self.assertEqual(starts,[None,'upgraded-thread'])
        self.assertEqual(app.agent.data['tool_signature'],TOOL_SIGNATURE)
        self.assertEqual(app.agent.data['previous_threads'],['old-thread'])
        self.assertIn('之前我喜欢花园',prompts[0])
        self.assertNotIn('升级前最近对话记录',prompts[1])
        self.assertEqual(sum(m['role']=='notice' for m in app.agent.data['messages']),1)
        self.assertEqual(app.agent.data['messages'][0]['text'],'之前我喜欢花园。')
