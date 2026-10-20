import unittest
from unittest import mock

import test_manager as fixtures
from vampm.util import Job


class LibraryScopeTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def prepare(self):
        scene=self.put('A.Scene.1',folder='收藏')
        self.put('A.Scene.1',folder='收藏/副本',data=scene.read_bytes())
        self.put('B.Look.1',folder='收藏',files={'Custom/Atom/Person/Appearance/Look.vap':'{}'})
        self.put('C.OtherScene.1')
        self.scan()
        self.c.label('a.scene.1',summary='花园舞蹈',category='场景')
        self.c.label('b.look.1',summary='花园人物',category='人物')

    def test_counts_respect_favorites_search_and_state_but_facets_ignore_category(self):
        self.prepare()
        query={'scope':'favorites','q':'花园','category':'人物','with_counts':'1','limit':1}
        r=self.c.list(query)
        self.assertEqual(r['total'],1)
        self.assertEqual(r['facets'],{'total':2,'categories':{'场景':1,'人物':1}})
        self.assertEqual(r['summary']['packages'],1)
        self.assertEqual(r['summary']['files'],1)
        self.assertEqual(r['summary']['bytes'],self.c.packages()['b.look.1']['size'])
        r=self.c.list(dict(query,category=''))
        self.assertEqual(r['summary']['files'],3)
        self.assertEqual(r['summary']['duplicates'],1)
        self.assertEqual(r['summary']['bytes'],sum(row['size'] for row in self.c.rows() if row['pkgkey']!='c.otherscene.1'))
        self.assertEqual(len(r['items']),1)  # Totals include every result, not just this page.
        self.assertEqual(self.c.list({'state':'favorite'})['total'],2)
        self.assertNotIn('summary',self.c.list({}))  # Existing callers keep the compact response.
        self.assertEqual(self.c.list({'with_counts':'1'})['facets']['total'],3)

    def test_status_filter_and_bulk_keys_stay_within_favorites(self):
        self.prepare()
        plan=self.ops.plan('activate',['a.scene.1','c.otherscene.1'],Job('preview'))
        self.ops.apply(plan['id'],Job('activate'))
        query={'scope':'favorites','state':'active','with_counts':True}
        r=self.c.list(query)
        self.assertEqual([p['key'] for p in r['items']],['a.scene.1'])
        self.assertEqual(r['summary']['active'],1)
        self.assertEqual(r['facets']['categories'],{'场景':1})
        self.assertEqual(self.c.list(dict(query,keys_only=True))['keys'],['a.scene.1'])
        self.assertEqual(self.c.list(dict(query,state='disabled',keys_only=True))['keys'],['b.look.1'])
        self.assertEqual(self.c.list({'state':'active','keys_only':True})['keys'],['a.scene.1','c.otherscene.1'])

    def test_counts_invalidate_after_category_favorite_undo_and_scanning(self):
        self.prepare();query={'scope':'favorites','with_counts':True}
        self.assertEqual(self.c.list(query)['facets']['categories']['人物'],1)
        self.c.label('b.look.1',category='服饰')
        self.assertEqual(self.c.list(query)['facets']['categories'],{'场景':1,'服饰':1,'人物':1})
        removed=self.ops.favorite(['b.look.1'],False,Job('remove'))
        self.assertEqual(self.c.list(query)['total'],1)
        self.assertEqual(self.c.list(query)['summary']['files'],2)
        self.ops.undo(removed['operation'],Job('undo'))
        self.assertEqual(self.c.list(query)['total'],2)
        self.put('D.New.1',folder='收藏');self.scan()
        self.assertEqual(self.c.list(query)['facets']['total'],3)

    def test_last_page_returns_previous_page_after_favorite_removed(self):
        for i in range(41):self.put(f'A.Item{i:02d}.1',folder='收藏')
        self.scan();query={'scope':'favorites','page':2,'with_counts':True}
        last=self.c.list(query)
        self.assertEqual(last['page'],2);self.assertEqual(len(last['items']),1)
        self.ops.favorite([last['items'][0]['key']],False,Job('remove last'))
        r=self.c.list(query)
        self.assertEqual(r['page'],1);self.assertEqual(len(r['items']),40)
        self.assertEqual(r['facets']['total'],40)
        empty=self.c.list(dict(query,q='no such package',page=99))
        self.assertEqual(empty['page'],1);self.assertEqual(empty['items'],[])
        self.assertEqual(empty['summary']['bytes'],0)
        self.assertEqual(empty['facets'],{'total':0,'categories':{}})

    def test_count_queries_use_bounded_cache_without_rescanning_packages(self):
        self.prepare();query={'scope':'favorites','category':'人物','with_counts':True}
        first=self.c.list(query)
        with mock.patch.object(self.c,'_filtered',side_effect=AssertionError('Warm queries should reuse filtering')):
            self.assertEqual(self.c.list(dict(query,page=10)),first)
        for i in range(45):self.c.list(dict(query,q=str(i)))
        self.assertLessEqual(len(self.c._derived['queries']),32)
