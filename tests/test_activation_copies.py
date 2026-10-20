import json
import os
import unittest
from pathlib import Path

import test_manager as fixtures
from vampm.agent_tools import invoke, validate
from vampm.catalog import Catalog
from vampm.operations import Operations
from vampm.server import Application
from vampm.util import Job, sha256


class ActivationCopyTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan

    def variants(self):
        a = self.put('A.Scene.1', '原版', deps=['B.Hair.1'])
        b = self.put('A.Scene.1', '汉化', deps=['C.Clothes.1'])
        self.put('B.Hair.1'); self.put('C.Clothes.1'); self.put('D.Other.1')
        self.scan()
        return a, b

    def plan(self, **kwargs):
        return self.ops.plan('activate', ['a.scene.1'], Job('preview'), **kwargs)

    def inventory(self):
        return {(area, str(p.relative_to(root))): sha256(p)
                for area, root in self.c.roots.items() for p in root.rglob('*.var')}

    def active(self):
        return {key for key, p in self.c.packages().items() if p['active']}

    def test_unresolved_previews_union_without_any_moves(self):
        a, b = self.variants(); before = self.inventory()
        p = self.plan()
        self.assertEqual(p['included'], 3)
        self.assertEqual(p['unresolved_copies'], ['a.scene.1'])
        self.assertEqual(len(p['copy_conflicts'][0]['variants']), 2)
        self.assertEqual(p['count'], 0)
        with self.assertRaisesRegex(ValueError, '同名副本待选择'):
            self.ops.apply(p['id'], Job('apply'))
        self.assertEqual(before, self.inventory())
        self.assertFalse(self.ops.activation.path.exists())

    def test_all_copies_include_union_dependencies_and_undo_exactly(self):
        a, b = self.variants(); before = self.inventory()
        p = self.plan(duplicate_mode='all')
        self.assertFalse(p['unresolved_copies']); self.assertEqual(p['included_files'], 4)
        self.ops.apply(p['id'], Job('apply'))
        rows=self.c.packages()['a.scene.1']['files']
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['area']=='active' and '/副本-' in r['rel'] for r in rows))
        self.assertEqual(self.active(), {'a.scene.1', 'b.hair.1', 'c.clothes.1'})
        self.ops.undo(p['id'], Job('undo'))
        self.assertEqual(before, self.inventory())

    def test_selected_copy_uses_its_own_dependencies_and_survives_moves_restart(self):
        a, b = self.variants(); before = self.inventory(); digest = sha256(b)
        p = self.plan(duplicate_mode='select', copy_choices={'a.scene.1':digest}, remember_copies=True)
        self.assertEqual(p['included'], 2)
        self.ops.apply(p['id'], Job('apply'))
        self.assertEqual(self.active(), {'a.scene.1', 'c.clothes.1'})
        active=next(r for r in self.c.packages()['a.scene.1']['files'] if r['area']=='active')
        self.assertEqual(sha256(self.c.path(active)),digest)
        self.ops = Operations(Catalog(self.game, self.home))
        again = self.plan()
        self.assertEqual(again['count'], 0)
        self.assertEqual(again['copy_conflicts'][0]['selected'], digest)
        self.ops.undo(p['id'], Job('undo'))
        self.assertEqual(before, self.inventory())

    def test_byte_identical_auto_select_but_all_keeps_every_file(self):
        data = fixtures.var_bytes('A.Scene.1')
        self.put('A.Scene.1', 'a', data=data); self.put('A.Scene.1', 'b', data=data); self.scan()
        selected = self.plan()
        self.assertFalse(selected['unresolved_copies']); self.assertEqual(selected['included_files'], 1)
        self.assertTrue(selected['copy_conflicts'][0]['identical'])
        all_copies = self.plan(duplicate_mode='all', remember_copies=True)
        self.assertEqual(all_copies['count'], 2); self.assertEqual(all_copies['included_files'], 2)
        self.assertEqual(Operations(self.c).activation.policy()['mode'], 'all')

    def test_readable_copy_auto_selected_and_all_can_retain_invalid_copy(self):
        good = self.put('A.Scene.1', 'good')
        self.put('A.Scene.1', 'bad', data=b'<html>Access gate</html>'); self.scan()
        p = self.plan()
        self.assertFalse(p['missing']); self.assertFalse(p['unresolved_copies'])
        self.assertEqual(p['copy_conflicts'][0]['selected'], sha256(good))
        self.assertEqual(p['included_files'], 1)
        all_copies = self.plan(duplicate_mode='all')
        self.assertEqual(all_copies['included_files'], 2)
        self.assertTrue(any('不可读取' in w for w in all_copies['warnings']))

    def test_all_invalid_copies_still_blocked(self):
        self.put('A.Scene.1', 'a', data=b'invalid'); self.put('A.Scene.1', 'b', data=b'not zip'); self.scan()
        p = self.plan(duplicate_mode='all')
        self.assertTrue(p['missing'])
        self.assertEqual(p['included'], 0); self.assertEqual(p['included_files'], 0)
        with self.assertRaisesRegex(ValueError, '没有可读取'):
            self.ops.apply(p['id'], Job('apply'))

    def test_stale_choice_does_not_silently_switch_to_remaining_copy(self):
        a, b = self.variants()
        self.plan(copy_choices={'a.scene.1':sha256(b)}, remember_copies=True)
        b.unlink()
        p = self.plan()
        self.assertEqual(p['unresolved_copies'], ['a.scene.1'])
        self.assertTrue(p['copy_conflicts'][0]['stale'])
        corrected = self.plan(copy_choices={'a.scene.1':sha256(a)})
        self.assertFalse(corrected['unresolved_copies'])

    def test_per_profile_policies_independent_from_each_other_and_temporary(self):
        a, b = self.variants()
        p1 = self.ops.save_profile('one', ['a.scene.1'])[0]
        p2 = next(p for p in self.ops.save_profile('two', ['a.scene.1']) if p['name'] == 'two')
        for profile, file in [(p1, a), (p2, b)]:
            p = self.ops.plan('activate', [], Job('preview'), profile_id=profile['id'],
                              copy_choices={'a.scene.1':sha256(file)}, remember_copies=True)
            self.assertEqual(p['included'], 2)
        self.assertEqual(self.plan()['unresolved_copies'], ['a.scene.1'])
        self.plan(duplicate_mode='all', remember_copies=True)
        self.assertEqual(self.ops.activation.policy(p1['id'])['choices']['a.scene.1'], sha256(a))
        self.assertEqual(self.ops.activation.policy(p2['id'])['choices']['a.scene.1'], sha256(b))
        self.ops.delete_profile(p1['id'])
        saved = json.loads(self.ops.activation.path.read_text())
        self.assertNotIn(p1['id'], saved['profiles'])
        self.assertEqual(saved['selection']['mode'], 'all')

    def test_dynamic_profile_update_and_saved_policy_change_invalidate_preview(self):
        self.variants(); self.c.label('a.scene.1', favorite=True)
        profile = self.ops.save_profile('favorites', [], favorites=True)[0]
        p = self.ops.plan('activate', [], Job('preview'), profile_id=profile['id'], duplicate_mode='all')
        self.c.label('d.other.1', favorite=True)
        with self.assertRaisesRegex(ValueError, '收藏内容已更新'):
            self.ops.apply(p['id'], Job('apply'))
        p = self.plan(duplicate_mode='all')
        self.plan(duplicate_mode='all', remember_copies=True)
        with self.assertRaisesRegex(ValueError, '副本选择已更新'):
            self.ops.apply(p['id'], Job('apply'))

    def test_same_full_path_gets_separate_directory_without_filename_changes(self):
        a = self.put('A.Scene.1', '人物')
        b = self.c.roots['vault']/'人物/A.Scene.1.var'
        b.parent.mkdir(parents=True); b.write_bytes(fixtures.var_bytes('A.Scene.1', description='alternate'))
        self.scan(); before = self.inventory()
        p = self.plan(duplicate_mode='all')
        self.assertEqual(p['count'], 2)
        self.assertIn('/副本-', p['preview'][0]['to_rel'])
        self.assertEqual(Path(p['preview'][0]['to_rel']).name, b.name)
        self.ops.apply(p['id'], Job('apply'))
        self.assertEqual(len(list(self.c.roots['active'].rglob('*.var'))), 2)
        self.ops.undo(p['id'], Job('undo'))
        self.assertEqual(before, self.inventory())
        # Both directions originally collide. Sequential execution remains reversible.
        p = self.plan(copy_choices={'a.scene.1':sha256(b)})
        self.assertEqual(p['count'], 2)
        self.ops.apply(p['id'], Job('apply'))
        active = next(self.c.roots['active'].rglob('*.var'))
        self.assertEqual(sha256(active), before[('vault', str(b.relative_to(self.c.roots['vault'])))])
        self.ops.undo(p['id'], Job('undo'))
        self.assertEqual(before, self.inventory())

        restored = self.ops.plan('restore_all', [], Job('restore preview'))
        self.ops.apply(restored['id'], Job('restore'))
        self.assertEqual(len(list(self.c.roots['active'].rglob('A.Scene.1.var'))), 2)
        self.ops.undo(restored['id'], Job('undo restore'))
        self.assertEqual(before, self.inventory())

    def test_missing_dependency_allows_apply_and_choice_can_be_remembered(self):
        good = self.put('A.Scene.1', 'a', deps=['B.Missing.1'])
        self.put('A.Scene.1', 'b'); self.scan()
        digest=sha256(good)
        p = self.plan(copy_choices={'a.scene.1':digest}, remember_copies=True)
        self.assertEqual(p['missing'], ['B.Missing.1'])
        self.ops.apply(p['id'], Job('apply'))
        active=next(self.c.roots['active'].rglob('A.Scene.1.var'))
        self.assertEqual(sha256(active),digest)
        self.assertEqual(len(list(self.c.roots['active'].rglob('A.Scene.1.var'))),1)
        self.assertEqual(self.ops.activation.policy()['choices']['a.scene.1'], digest)
        self.assertEqual(self.plan(copy_choices={})['unresolved_copies'], ['a.scene.1'])

    def test_content_tampering_with_same_fingerprint_is_caught_before_move(self):
        a, b = self.variants()
        p = self.plan(duplicate_mode='all')
        st = a.stat(); data = bytearray(a.read_bytes()); data[-1] ^= 1
        a.write_bytes(data); os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, '保留副本已变化'):
            self.ops.apply(p['id'], Job('apply'))
        self.assertTrue(a.exists() and b.exists())

    def test_bad_choice_arguments_rejected_in_api_and_agent(self):
        self.variants()
        for options in [{'duplicate_mode':'merge'}, {'copy_choices':[]},
                        {'copy_choices':{'a.scene.1':'../../anything'}}, {'remember_copies':'yes'}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError): self.plan(**options)
                with self.assertRaises(ValueError): validate('plan_files', dict(kind='activate', **options))
        with self.assertRaises(ValueError):
            self.ops.plan('move', ['a.scene.1'], Job('preview'), folder='x', duplicate_mode='all')

    def test_agent_can_preview_choose_apply_and_restore(self):
        a, b = self.variants(); before = self.inventory()
        app = Application(self.game, self.home); app.c.scan(Job('scan'))
        p = invoke(app, 'plan_files', {'kind':'activate', 'keys':['a.scene.1']})
        self.assertEqual(p['unresolved_copies'], ['a.scene.1'])
        chosen = next(v for v in p['copy_conflicts'][0]['variants'] if v['sha256'] == sha256(b))
        p = invoke(app, 'plan_files', {'kind':'activate', 'keys':['a.scene.1'], 'duplicate_mode':'select',
                                       'copy_choices':{'a.scene.1':chosen['sha256']}, 'remember_copies':True})
        invoke(app, 'apply_plan', {'id':p['id']})
        active=next(self.c.roots['active'].rglob('A.Scene.1.var'))
        self.assertEqual(sha256(active),chosen['sha256'])
        invoke(app, 'library_task', {'action':'undo', 'id':p['id']})
        self.assertEqual(before, self.inventory())
