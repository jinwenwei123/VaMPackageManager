import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from vampm.operations import move_no_replace
from vampm.util import Job,sha256


class BundleTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def execute(self,kind,keys=None,**kw):
        p=self.ops.plan(kind,keys or [],Job('preview'),**kw)
        result=self.ops.apply(p['id'],Job('apply'));return p,result

    def prepare(self,folder='收藏/人物卡/Bundle.Look.1'):
        self.put('Bundle.Look.1',folder,files={'Custom/Atom/Person/Appearance/a.vap':'{}'})
        self.put('Outfit.Dress.1',folder,files={'Custom/Clothing/Female/a.vam':'{}'})
        path=self.game/'AddonPackages'/folder/'Images';path.mkdir()
        (path/'Preview.png').write_bytes(b'preview')
        (path.parent/'说明.txt').write_bytes(b'readme')
        self.put('Other.Loose.1');self.scan()
        return path.parent

    def test_stage_keeps_folder_and_attachments_in_one_primary_category(self):
        original=self.prepare();before={r['id']:sha256(self.c.path(r)) for r in self.c.rows()}
        plan,_=self.execute('stage_all')
        target=self.c.roots['vault']/'收藏/人物/Bundle.Look.1'
        self.assertEqual((target/'Images/Preview.png').read_bytes(),b'preview')
        self.assertTrue((target/'说明.txt').exists())
        self.assertTrue((target/'Bundle.Look.1.var').exists())
        self.assertTrue((target/'Outfit.Dress.1.var').exists())
        self.assertFalse((target.parent/'Bundle.Look.1.var').exists())
        self.assertFalse(list(self.c.roots['vault'].rglob('人物卡')))
        self.assertEqual(before,{r['id']:sha256(self.c.path(r)) for r in self.c.rows()})
        self.assertIn('服饰',self.c.packages()['outfit.dress.1']['categories'])
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual((original/'Images/Preview.png').read_bytes(),b'preview')
        self.assertTrue((original/'Bundle.Look.1.var').is_file())

    def test_favorite_toggle_transfers_the_folder_and_documents(self):
        self.prepare('原始/Bundle.Look.1');self.execute('stage_all')
        self.ops.favorite(['bundle.look.1'],True,Job('favorite'))
        target=self.c.roots['vault']/'收藏/人物/Bundle.Look.1'
        self.assertTrue((target/'Images/Preview.png').is_file())
        self.assertTrue((target/'Outfit.Dress.1.var').is_file())
        result=self.ops.favorite(['bundle.look.1'],False,Job('unfavorite'))
        target=self.c.roots['vault']/'人物/Bundle.Look.1'
        self.assertTrue((target/'Images/Preview.png').is_file())
        self.assertTrue((target/'Bundle.Look.1.var').is_file())
        self.assertFalse(self.c.packages()['outfit.dress.1']['favorite'])
        self.ops.undo(result['operation'],Job('undo'))
        self.assertTrue((self.c.roots['vault']/'收藏/人物/Bundle.Look.1/说明.txt').is_file())

    def test_activation_includes_bundled_vars_and_follows_documents_then_undoes(self):
        self.prepare();self.execute('stage_all')
        plan,_=self.execute('activate',['bundle.look.1'])
        self.assertEqual(plan['included'],2)
        target=self.c.roots['active']/'收藏/人物/Bundle.Look.1'
        self.assertTrue((target/'Outfit.Dress.1.var').exists())
        self.assertTrue((target/'Images/Preview.png').exists())
        self.assertFalse(self.c.packages()['other.loose.1']['active'])
        self.ops.undo(plan['id'],Job('undo'))
        self.assertTrue((self.c.roots['vault']/'收藏/人物/Bundle.Look.1/说明.txt').exists())

    def test_fixed_dependency_stays_base_while_owner_folder_moves(self):
        self.prepare();self.execute('pin',['outfit.dress.1'])
        self.execute('stage_all')
        self.assertTrue((self.c.roots['active']/'基础包/Outfit.Dress.1.var').exists())
        self.assertTrue((self.c.roots['vault']/'收藏/人物/Bundle.Look.1/Bundle.Look.1.var').exists())
        self.ops.favorite(['bundle.look.1'],False,Job('unfavorite'))
        self.assertTrue((self.c.roots['active']/'基础包/Outfit.Dress.1.var').exists())
        self.assertTrue((self.c.roots['vault']/'人物/Bundle.Look.1/Images/Preview.png').exists())

    def test_duplicate_named_groups_keep_separate_subfolders_and_all_bytes(self):
        for folder,content in [('A/Bundle.Look.1',b'first'),('B/Bundle.Look.1',b'second')]:
            self.put('Bundle.Look.1',folder,description=content.decode())
            (self.c.roots['active']/folder/'readme.txt').write_bytes(content)
        self.scan();self.execute('stage_all')
        root=self.c.roots['vault']/'场景/Bundle.Look.1'
        dirs=list(root.glob('副本-*'));self.assertEqual(len(dirs),2)
        self.assertCountEqual([(d/'readme.txt').read_bytes() for d in dirs],[b'first',b'second'])
        self.assertTrue(all((d/'Bundle.Look.1.var').is_file() for d in dirs))

    def test_rename_collection_preserves_attachment_links_and_undo(self):
        self.prepare();self.execute('stage_all')
        renamed=self.ops.rename_collection('我的收藏',Job('rename'))
        plan,_=self.execute('activate',['bundle.look.1'])
        root=self.c.roots['active']/'我的收藏/人物/Bundle.Look.1'
        self.assertEqual((root/'Images/Preview.png').read_bytes(),b'preview')
        self.assertTrue((root/'Outfit.Dress.1.var').is_file())
        self.ops.undo(plan['id'],Job('undo activation'))
        self.ops.undo(renamed['operation'],Job('undo rename'))
        self.execute('activate',['bundle.look.1'])
        root=self.c.roots['active']/'收藏/人物/Bundle.Look.1'
        self.assertEqual((root/'说明.txt').read_bytes(),b'readme')
        self.assertTrue((root/'Images/Preview.png').is_file())

    def test_pin_main_keeps_documents_and_unpin_returns_folder(self):
        self.prepare();self.execute('stage_all');self.execute('pin',['bundle.look.1'])
        root=self.c.roots['active']/'基础包/Bundle.Look.1'
        self.assertTrue((root/'Bundle.Look.1.var').is_file())
        self.assertEqual((root/'说明.txt').read_bytes(),b'readme')
        self.execute('unpin',['bundle.look.1'])
        root=self.c.roots['vault']/'收藏/人物/Bundle.Look.1'
        self.assertTrue((root/'Bundle.Look.1.var').is_file())
        self.assertEqual((root/'说明.txt').read_bytes(),b'readme')

    def test_interrupted_bundle_move_recovers_original_inventory(self):
        self.prepare();before={str(p):sha256(p) for p in self.c.roots['active'].rglob('*') if p.is_file()}
        plan=self.ops.plan('stage_all',[],Job('preview'));count=0
        def fail(src,dst,job=None):
            nonlocal count
            count+=1
            if count==5:raise OSError('interrupted')
            move_no_replace(src,dst)
        with mock.patch('vampm.operations.move_no_replace',side_effect=fail):
            with self.assertRaises(OSError):self.ops.apply(plan['id'],Job('apply'))
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(before,{str(p):sha256(p) for p in self.c.roots['active'].rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
