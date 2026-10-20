import json
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.catalog import Catalog
from vampm.operations import Operations,move_no_replace
from vampm.util import Job,sha256


class MultiClassLaunchTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def execute(self,kind,keys=None,**kwargs):
        plan=self.ops.plan(kind,keys or [],Job('preview'),**kwargs)
        self.ops.apply(plan['id'],Job('apply'));return plan

    def test_overlapping_content_tags_include_presets_but_not_dependencies(self):
        self.put('Mix.Look.1',deps=['External.Hair.1'],files={
            'Saves/scene/look.json':'{}','Custom/Atom/Person/Appearance/look.vap':'{}',
            'Custom/Atom/Person/Clothing/outfit.vap':'{}','Custom/Atom/Person/Hair/style.vap':'{}',
            'Custom/Atom/Person/Morphs/body.vap':'{}','Custom/Atom/Person/Skin/skin.vap':'{}',
            'Custom/Atom/Person/Plugins/scripts.vap':'{}','Custom/Atom/Person/Pose/pose.vap':'{}'})
        self.put('A.Scene.1',deps=['External.Hair.1']);self.scan()
        p=self.c.packages()['mix.look.1']
        self.assertEqual(set(p['categories']),{'场景','人物','服饰','头发','变形','皮肤纹理','插件','姿势动作'})
        for cat in p['categories']:self.assertIn('mix.look.1',self.c.list({'category':cat,'keys_only':True})['keys'])
        self.assertNotIn('头发',self.c.packages()['a.scene.1']['categories'])
        counts=self.c.list({'with_counts':True})['facets'];self.assertEqual(counts['total'],2)
        self.assertEqual(counts['categories']['场景'],2)
        self.c.label('mix.look.1',favorite=True)
        self.assertEqual(self.c.list({'scope':'favorites','with_counts':True})['facets']['total'],1)

    def test_same_uid_unions_content_categories_and_manual_primary_remains_single_home(self):
        self.put('Mix.Look.1','one',files={'Custom/Clothing/a.vam':'{}'})
        self.put('Mix.Look.1','two',files={'Saves/scene/look.json':'{}'});self.scan()
        self.c.label('mix.look.1',category='人物',summary='保留说明')
        self.assertEqual(set(self.c.packages()['mix.look.1']['categories']),{'人物','场景','服饰'})
        self.execute('organize',['mix.look.1'])
        rows=self.c.packages()['mix.look.1']['files']
        self.assertTrue(all(r['rel'].startswith('人物/Mix.Look.1/副本-') for r in rows))
        self.assertEqual(len({r['rel'] for r in rows}),2)
        again=self.ops.plan('organize',['mix.look.1'],Job('repeat'));self.assertEqual(again['count'],0)

    def test_classification_upgrade_preserves_deep_dependencies_without_reading_scene_bodies(self):
        path=self.put(files={'Saves/scene/Test.json':'"Secret.Dependency.1:/file"','Custom/Atom/Person/Clothing/a.vap':'{}'})
        self.scan(True)
        with self.c.db() as db:
            row=db.execute('SELECT id,info FROM files').fetchone();info=json.loads(row['info'])
            info.pop('content_revision');info['counts']['clothing']=0
            db.execute('UPDATE files SET info=? WHERE id=?',(json.dumps(info),row['id']))
        self.c.invalidate()
        import vampm.catalog as module
        real=module.inspect_var
        with mock.patch.object(module,'inspect_var',wraps=real) as reader:self.scan()
        self.assertFalse(reader.call_args.args[1])
        p=self.c.packages()['author.scene.1'];self.assertEqual(p['deep'],1)
        self.assertIn('Secret.Dependency.1',p['deps']);self.assertIn('服饰',p['categories'])
        self.assertTrue(path.exists())

    def prepare(self):
        self.put('A.Favorite.1',folder='收藏/旧目录',deps=['B.Hair.1','Unavailable.X.1'])
        self.put('B.Hair.1',files={'Custom/Hair/Female/b.vam':'{}'})
        self.put('C.New.1',deps=['D.Asset.1'])
        self.put('D.Asset.1');self.put('E.Other.1')
        note=self.c.roots['active']/'收藏/旧目录/说明.txt';note.write_bytes(b'notes')
        self.scan();pid=self.ops.ensure_default_profile()
        return pid,note

    def test_stage_all_preserves_sidecars_and_undo_restores_original_inventory(self):
        pid,note=self.prepare()
        original={r['id']:(r['area'],r['rel'],sha256(self.c.path(r))) for r in self.c.rows()}
        plan=self.execute('stage_all')
        self.assertEqual(self.c.stats()['active'],0);self.assertEqual(plan['attachment_count'],1)
        self.assertFalse(note.exists());self.assertEqual((self.c.roots['vault']/'收藏/场景/A.Favorite.1/说明.txt').read_bytes(),b'notes')
        self.assertTrue((self.c.roots['vault']/'收藏/场景/A.Favorite.1/A.Favorite.1.var').exists())
        self.assertTrue((self.c.roots['vault']/'头发/B.Hair.1.var').exists())
        self.assertEqual(self.ops.import_target()['area'],'vault')
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(original,{r['id']:(r['area'],r['rel'],sha256(self.c.path(r))) for r in self.c.rows()})
        self.assertEqual(note.read_bytes(),b'notes')

    def test_default_favorites_add_remove_syncs_dependencies_and_is_reversible(self):
        pid,note=self.prepare();self.execute('stage_all')
        active=self.execute('activate',profile_id=pid)
        self.assertTrue(active['missing']);self.assertTrue(self.ops.follows_favorites())
        self.assertEqual(self.c.stats()['active'],2);self.assertTrue((self.c.roots['active']/'收藏/场景/A.Favorite.1/说明.txt').exists())
        result=self.ops.favorite(['c.new.1'],True,Job('favorite'))
        self.assertTrue(result['auto_sync']);self.assertEqual(self.c.stats()['active'],4)
        self.assertTrue((self.c.roots['active']/'收藏/场景/C.New.1.var').exists())
        self.assertTrue(self.c.packages()['d.asset.1']['active']);self.assertFalse(self.c.packages()['d.asset.1']['favorite'])
        self.assertFalse(self.c.packages()['e.other.1']['active'])
        removed=self.ops.favorite(['c.new.1'],False,Job('remove'))
        self.assertEqual(self.c.stats()['active'],2)
        self.assertTrue((self.c.roots['vault']/'场景/C.New.1.var').exists())
        self.ops.undo(removed['operation'],Job('undo'));self.assertEqual(self.c.stats()['active'],4)
        self.ops.undo(result['operation'],Job('undo'));self.assertEqual(self.c.stats()['active'],2)

    def test_unfavorite_needed_dependency_stays_active_in_general_category(self):
        pid,_=self.prepare();self.ops.favorite(['b.hair.1'],True,Job('star'))
        self.execute('activate',profile_id=pid)
        self.ops.favorite(['b.hair.1'],False,Job('unstar'))
        self.assertTrue(self.c.packages()['b.hair.1']['active'])
        self.assertTrue((self.c.roots['active']/'头发/B.Hair.1.var').exists())

    def test_switching_profiles_stops_autostart_and_manual_favorite_scan_can_sync(self):
        pid,_=self.prepare();self.execute('activate',profile_id=pid)
        new=self.c.roots['vault']/'收藏/临时/New.Manual.1.var';new.parent.mkdir(parents=True,exist_ok=True)
        new.write_bytes(fixtures.var_bytes('New.Manual.1'));self.scan();self.ops.sync_favorites(Job('sync'))
        self.assertTrue(self.c.packages()['new.manual.1']['active'])
        other=self.ops.save_profile('其他',['e.other.1'])[-1];self.execute('activate',profile_id=other['id'])
        self.assertFalse(self.ops.follows_favorites())
        self.ops.favorite(['c.new.1'],True,Job('star'))
        self.assertFalse(self.c.packages()['c.new.1']['active'])
        self.assertFalse(self.c.packages()['d.asset.1']['active'])

    def test_interrupted_stage_restores_sidecars_and_all_original_bytes(self):
        self.prepare();before={str(p):sha256(p) for p in self.c.roots['active'].rglob('*') if p.is_file()}
        plan=self.ops.plan('stage_all',[],Job('preview'));count=0
        def fail(src,dst,job=None):
            nonlocal count
            count+=1
            if count==4:raise OSError('simulated failure')
            move_no_replace(src,dst)
        with mock.patch('vampm.operations.move_no_replace',side_effect=fail):
            with self.assertRaises(OSError):self.ops.apply(plan['id'],Job('apply'))
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(before,{str(p):sha256(p) for p in self.c.roots['active'].rglob('*') if p.is_file()})

    def test_preview_rejects_changed_primary_category_and_default_is_persistent(self):
        pid,_=self.prepare();plan=self.ops.plan('stage_all',[],Job('plan'))
        self.c.label('c.new.1',category='人物')
        with self.assertRaisesRegex(ValueError,'主分类'):self.ops.apply(plan['id'],Job('apply'))
        self.assertEqual(Operations(Catalog(self.game,self.home)).ensure_default_profile(),pid)
        with self.assertRaises(ValueError):self.ops.delete_profile(pid)
