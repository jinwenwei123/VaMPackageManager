import json
import unittest

import test_manager as fixtures
from vampm.agent_tools import TOOLS
from vampm.providers import AI
from vampm.util import CATEGORIES, Job


class ContentCategories(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def test_legacy_dependency_labels_use_content_without_rewriting_data_or_closure(self):
        self.put('A.Scene.1',deps=['Hair.Style.1','Wear.Shirt.1'])
        self.put('Hair.Style.1',files={'Custom/Hair/Female/Hair.vam':'{}'})
        self.put('Wear.Shirt.1',files={'Custom/Clothing/Female/Shirt.vam':'{}'})
        self.scan()
        with self.c.db() as db:
            for key in ('hair.style.1','wear.shirt.1'):
                db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?)',(key,'依赖','保留这条原有简介。','ai',1,'before'))
            row=db.execute("SELECT id,info FROM files WHERE pkgkey='wear.shirt.1'").fetchone()
            info=json.loads(row['info']);info['category']='依赖'
            db.execute('UPDATE files SET info=? WHERE id=?',(json.dumps(info),row['id']))
            before=[tuple(r) for r in db.execute('SELECT * FROM labels ORDER BY pkgkey')]
        self.c.invalidate()
        pkgs=self.c.packages()
        self.assertEqual(pkgs['hair.style.1']['category'],'头发')
        self.assertEqual(pkgs['wear.shirt.1']['category'],'服饰')
        self.assertEqual(pkgs['hair.style.1']['summary'],'保留这条原有简介。')
        self.assertTrue(pkgs['wear.shirt.1']['favorite'])
        self.assertNotIn('依赖',self.c.stats()['categories_all'])
        self.assertEqual(self.c.list({'category':'服饰'})['total'],1)
        self.assertEqual(set(self.c.closure(['a.scene.1'])['keys']),{'a.scene.1','hair.style.1','wear.shirt.1'})
        self.c.label('wear.shirt.1',favorite=False)
        self.assertEqual(self.c.packages()['wear.shirt.1']['category'],'服饰')
        with self.c.db() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM labels WHERE pkgkey='hair.style.1'").fetchone()),before[0])
        self.scan();self.assertEqual(self.c.packages()['wear.shirt.1']['category'],'服饰')

    def test_unknown_dependency_content_falls_back_to_other(self):
        self.put('A.Empty.1',files={'Assets/notice.txt':'info'});self.scan()
        with self.c.db() as db:
            db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?)',('a.empty.1','依赖','原说明。','manual',0,'before'))
        self.c.invalidate()
        self.assertEqual(self.c.detail('a.empty.1')['category'],'其他')
        self.assertEqual(self.c.detail('a.empty.1')['summary'],'原说明。')

    def test_ai_manual_and_agent_schemas_only_accept_content_types(self):
        self.put();self.scan()
        self.assertNotIn('依赖',CATEGORIES)
        for tool in TOOLS:
            props=tool['inputSchema']['properties']
            if 'category' in props:self.assertNotIn('依赖',props['category']['enum'])
        with self.assertRaises(ValueError):self.c.label('author.scene.1',category='依赖')
        with self.assertRaises(ValueError):AI.validate({'items':[{'key':'a','category':'依赖','summary':'公共资源。'}]},{'a'})
