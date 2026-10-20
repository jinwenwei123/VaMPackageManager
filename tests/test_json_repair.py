import io
import json
import os
import zipfile
from types import SimpleNamespace
from unittest import TestCase,mock

import test_manager as fixtures
from vampm.json_repair import JsonRepairs,validate_edits,strict_json
from vampm.package_repair import PackageRepairs
from vampm.catalog import inspect_var
from vampm.util import Job,read_json,sha256
from vampm import zip_metadata

BASE='{"creatorName":"A","packageName":"Scene","dependencies":{"D.Hair.1":{}},"instructions":"Keep this text"}'
BAD=BASE[:-1]+',}'


def archive(text,descriptor=False):
    class Unseekable(io.BytesIO):
        def seekable(self):return False
        def seek(self,*args):raise io.UnsupportedOperation()
    stream=Unseekable() if descriptor else io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        z.comment=b'archive comment'
        z.writestr('Saves/scene/Test.json','{"file":"Other.Asset.2:/Custom/x"}')
        z.writestr('meta.json',text)
        info=zipfile.ZipInfo('Custom/Textures/test.bin');info.compress_type=zipfile.ZIP_STORED;info.comment=b'entry comment'
        z.writestr(info,bytes(range(256))*400)
    return stream.getvalue()


class JsonRepairTests(TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def service(self):
        calls=[]
        def complete(cfg,instruction,evidence,schema,job,model,**options):
            calls.append((model,evidence))
            if isinstance(evidence,list):return {'items':[{'id':r['id'],'filename':'A.Name.2.var','reason':'移除下载后缀','confidence':'high'} for r in evidence]}
            return {'can_repair':True,'reason':'删除对象末尾逗号','edits':[{'before':',}', 'after':'}'}]}
        ai=SimpleNamespace(settings=lambda private:{'provider':'deepseek','api_summary_model':'summary','api_model':'chat'},complete_json=complete)
        service=JsonRepairs(self.c,self.ops,ai);service.calls=calls;return service

    def setup_bad(self,text=BAD):
        path=self.put('A.Scene.1',data=archive(text));self.scan();return path

    def analyze(self,s):return s.analyze(Job('analyze'))
    def apply(self,s,p,ids=None):return s.apply(p['id'],ids or [x['id'] for x in p['items'] if x['repairable']],Job('apply'))

    def test_inventory_includes_tolerated_metadata_excludes_bad_zip_and_size_warnings(self):
        self.setup_bad();self.put('A.MissingBrace.1',data=archive(BASE[:-1]));self.put('A.Broken.1',data=b'broken')
        valid=self.put('A.Valid.1');self.scan()
        row=self.c.packages()['a.valid.1']['files'][0]
        with self.c.db() as db:db.execute('INSERT INTO file_warnings VALUES(?,?,?,?)',(row['id'],row['size'],row['mtime'],'["发布体积不同"]'))
        service=self.service();rows,skipped=service.inventory()
        self.assertEqual(len(rows),2);self.assertEqual(len(skipped),2)
        self.assertTrue(any('ZIP' in r['reason'] for r in skipped));self.assertTrue(any('未报告 JSON' in r['reason'] for r in skipped))

    def test_preview_uses_summary_model_and_does_not_change_archive(self):
        path=self.setup_bad();digest=sha256(path);service=self.service();p=self.analyze(service)
        self.assertEqual(p['model'],'summary');self.assertEqual(service.calls[0][0],'summary')
        self.assertTrue(p['items'][0]['repairable']);self.assertIn('修复后',p['items'][0]['diff']);self.assertEqual(sha256(path),digest)
        self.assertFalse(list(self.ops.directory.glob('*/manifest.json')))
        self.assertEqual(service.last_preview()['id'],p['id'])

    def test_repair_preserves_assets_identity_and_byte_exact_undo(self):
        path=self.setup_bad();digest=sha256(path);before=path.read_bytes();row=self.c.packages()['a.scene.1']['files'][0]
        self.c.label('a.scene.1',summary='人工说明',favorite=True,source='manual',tags=['舞蹈','带音乐'])
        service=self.service();result=self.apply(service,self.analyze(service))
        package=self.c.packages()['a.scene.1'];self.assertEqual(package['error'],'');self.assertTrue(package['favorite'])
        self.assertTrue(package['summary_generated']);self.assertEqual(package['files'][0]['id'],row['id']);self.assertEqual(package['area'],row['area'])
        self.assertEqual(package['deps'],['D.Hair.1'])
        self.assertEqual(package['tags'],['舞蹈','带音乐']);self.assertTrue(package['tags_generated'])
        with zipfile.ZipFile(io.BytesIO(before)) as old,zipfile.ZipFile(path) as new:
            for name in old.namelist():
                if name!='meta.json':self.assertEqual(old.read(name),new.read(name))
            self.assertEqual(new.comment,old.comment);self.assertEqual(new.getinfo('Custom/Textures/test.bin').comment,b'entry comment')
        folder=self.ops.directory/result['operation']
        self.assertLess(sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()),len(before))
        self.assertNotIn('json_repair_state',self.ops.history()[0])
        self.ops.undo(result['operation'],Job('undo'));self.assertEqual(sha256(path),digest)
        self.assertEqual(path.stat().st_mtime_ns,row['mtime']);self.assertTrue(self.c.packages()['a.scene.1']['summary_generated'])
        self.assertEqual(self.c.detail('a.scene.1')['tags'],['舞蹈','带音乐']);self.assertTrue(self.c.detail('a.scene.1')['tags_generated'])

    def test_pinned_location_and_download_warning_survive(self):
        path=self.put('A.Scene.1','基础包',data=archive(BAD));self.scan();self.ops.base.save({'roots':['a.scene.1']})
        row=self.c.packages()['a.scene.1']['files'][0]
        with self.c.db() as db:db.execute('INSERT INTO file_warnings VALUES(?,?,?,?)',(row['id'],row['size'],row['mtime'],json.dumps(['发布体积不同','meta.json 末尾缺少闭合括号，已兼容读取'])))
        s=self.service();self.apply(s,self.analyze(s));p=self.c.packages()['a.scene.1']
        self.assertTrue(p['pinned']);self.assertTrue(path.exists());self.assertEqual(p['warnings'],['发布体积不同'])

    def test_changed_source_and_invalid_selections_rejected_before_writes(self):
        path=self.setup_bad();s=self.service();p=self.analyze(s)
        with self.assertRaises(ValueError):self.apply(s,p,[999])
        path.write_bytes(archive(BASE))
        with self.assertRaises(ValueError):self.apply(s,p)
        self.assertFalse(list(self.ops.directory.glob('*/manifest.json')))

    def test_unsafe_ai_edits_are_visible_but_not_applicable(self):
        self.setup_bad();s=self.service()
        s.ai.complete_json=lambda *a,**kw:{'can_repair':True,'reason':'改名','edits':[{'before':BAD,'after':BASE.replace('A','Evil')}]}
        p=self.analyze(s);self.assertFalse(p['items'][0]['repairable']);self.assertIn('原始数据',p['items'][0]['reason'])
        with self.assertRaises(ValueError):self.apply(s,p,[p['items'][0]['id']])

    def test_partial_swap_failure_recoverable_after_rescan(self):
        path=self.setup_bad();digest=sha256(path);s=self.service();p=self.analyze(s)
        with mock.patch.object(s,'_sync',side_effect=OSError('interrupted after replace')):
            with self.assertRaises(OSError):self.apply(s,p)
        state=self.ops.history()[0];self.assertEqual(state['status'],'interrupted');self.scan()
        self.ops.undo(state['id'],Job('undo'));self.assertEqual(sha256(path),digest)

    def test_prepare_failure_does_not_touch_source_and_recovery_cleans_temp(self):
        path=self.setup_bad();digest=sha256(path);s=self.service();p=self.analyze(s)
        def fail(src,temp,*args):temp.write_bytes(b'partial');raise OSError('full disk')
        with mock.patch('vampm.json_repair.zip_metadata.rewrite',side_effect=fail):
            with self.assertRaises(OSError):self.apply(s,p)
        self.assertEqual(sha256(path),digest);self.ops.undo(self.ops.history()[0]['id'],Job('recover'))
        self.assertFalse(list(path.parent.glob('*.repair-tmp')))

    def test_changed_post_repair_content_blocks_undo(self):
        path=self.setup_bad();s=self.service();result=self.apply(s,self.analyze(s));path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'内容已变化'):self.ops.undo(result['operation'],Job('undo'))
        self.assertEqual(path.read_bytes(),b'changed')

    def test_resume_does_not_repeat_finished_ai_calls(self):
        self.setup_bad();self.put('A.Second.1',data=archive(BAD));self.scan();s=self.service();original=s.ai.complete_json
        def fail(*args,**kwargs):
            if len(s.calls)==1:raise ConnectionResetError('network')
            return original(*args,**kwargs)
        s.ai.complete_json=fail
        with self.assertRaises(ConnectionResetError):self.analyze(s)
        self.assertEqual(s.status()['analyzed'],1);s.ai.complete_json=original
        self.assertEqual(len(self.analyze(s)['items']),2);self.assertEqual(len(s.calls),2)

    def test_unified_preview_and_selected_type_only(self):
        path=self.setup_bad();bad_name=self.put('A.Name.2_2',data=fixtures.var_bytes('A.Name.2'));self.scan()
        s=self.service();manager=PackageRepairs(self.c,self.ops,s.ai);p=manager.analyze(Job('preview'))
        self.assertEqual({i['type'] for i in p['items']},{'name','json'})
        chosen=next(i for i in p['items'] if i['type']=='json')
        result=manager.apply(p['id'],[{'type':'json','id':chosen['id']}],Job('apply'))
        self.assertEqual(result['repaired'],1);self.assertTrue(bad_name.exists());self.assertFalse(inspect_var(path)['error'])

    def test_zip_data_descriptors_bom_and_zip64_preserved_on_restore(self):
        for descriptor in (False,True):
            path=self.setup_bad(text=b'\xef\xbb\xbf'+BAD.encode())
            path.write_bytes(archive(b'\xef\xbb\xbf'+BAD.encode(),descriptor));self.scan();old=path.read_bytes()
            s=self.service();r=self.apply(s,self.analyze(s))
            with zipfile.ZipFile(path) as z:self.assertTrue(z.read('meta.json').startswith(b'\xef\xbb\xbf'))
            self.ops.undo(r['operation'],Job('undo'));self.assertEqual(path.read_bytes(),old)
        # Forced ZIP64 local headers even for small entries.
        path=self.c.roots['active']/'A.Scene.1.var'
        with zipfile.ZipFile(path,'w') as z:
            with z.open('meta.json','w',force_zip64=True) as f:f.write(BAD.encode())
            with z.open('Custom/test.bin','w',force_zip64=True) as f:f.write(b'content')
        self.scan();old=path.read_bytes();s=self.service();r=self.apply(s,self.analyze(s))
        self.ops.undo(r['operation'],Job('undo'));self.assertEqual(path.read_bytes(),old)

    def test_repaired_hash_keeps_existing_copy_selection_usable(self):
        path=self.setup_bad();s=self.service();old=sha256(path)
        self.ops.activation.save({'mode':'select','choices':{'a.scene.1':old}})
        result=self.apply(s,self.analyze(s))
        self.put('A.Scene.1','another',description='another valid variant');self.scan()
        closure=self.ops.activation.resolve(['a.scene.1'],self.ops.activation.policy(),Job('resolve'))
        self.assertFalse(closure['unresolved']);self.assertEqual(closure['policy']['choices']['a.scene.1'],sha256(path))
        self.ops.undo(result['operation'],Job('undo'))
        self.assertEqual(read_json(self.c.data/'repaired-copy-hashes.json'),{})

    def test_undo_preserves_later_manual_summary(self):
        path=self.setup_bad();s=self.service();r=self.apply(s,self.analyze(s))
        self.c.label('a.scene.1',summary='修复后手写的说明',source='manual')
        self.ops.undo(r['operation'],Job('undo'))
        p=self.c.packages()['a.scene.1'];self.assertEqual(p['summary'],'修复后手写的说明');self.assertTrue(p['summary_generated'])

    def test_two_types_in_one_confirmation_have_separate_undo_records(self):
        path=self.setup_bad();digest=sha256(path);name=self.put('A.Name.2_2',data=fixtures.var_bytes('A.Name.2'));self.scan()
        s=self.service();manager=PackageRepairs(self.c,self.ops,s.ai);p=manager.analyze(Job('preview'))
        selected=[{'type':i['type'],'id':i['id'],**({'filename':i['filename']} if i['type']=='name' else {})} for i in p['items']]
        result=manager.apply(p['id'],selected,Job('apply'));self.assertEqual(result['repaired'],2)
        for op in reversed(result['operations']):self.ops.undo(op['operation'],Job('undo'))
        self.assertEqual(sha256(path),digest);self.assertTrue(name.exists())

    def test_zip_backed_path_candidate_is_reviewed_and_validated(self):
        from vampm.json_repair import path_escape_edits
        text='{"creatorName":"A","contentList":["Custom' + chr(92) + 'hair.vam"]}'
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            z.writestr('meta.json',text);z.writestr('Custom/hair.vam','{}')
        path=self.put('A.Scene.1',data=buffer.getvalue());self.scan()
        edits=path_escape_edits(text,path);self.assertEqual(len(edits),1)
        fixed=validate_edits(text,edits);self.assertEqual(json.loads(fixed)['contentList'],['Custom'+chr(92)+'hair.vam'])
        s=self.service()
        def accept(cfg,instruction,evidence,*args,**kwargs):
            self.assertEqual(evidence['suggested_edits'],edits)
            return {'can_repair':True,'use_candidate':True,'reason':'原路径对应 ZIP 文件，正确转义','edits':[]}
        s.ai.complete_json=accept;p=self.analyze(s);self.assertTrue(p['items'][0]['repairable'])
        result=self.apply(s,p);self.assertFalse(inspect_var(path)['error']);self.ops.undo(result['operation'],Job('undo'))
        self.assertEqual(path.read_bytes(),buffer.getvalue())

    def test_truncated_reply_is_listed_and_other_packages_continue(self):
        from vampm.ai_response import AIResponseError
        self.setup_bad();self.put('A.Next.1',data=archive(BAD));self.scan();s=self.service();complete=s.ai.complete_json;count=0
        def truncated(*args,**kwargs):
            nonlocal count
            count+=1
            if count==1:raise AIResponseError('输出不完整',finish_reason='length')
            return complete(*args,**kwargs)
        s.ai.complete_json=truncated;p=self.analyze(s)
        self.assertTrue(p['complete']);self.assertEqual([i['repairable'] for i in p['items']],[False,True])

    def test_json_and_name_repair_same_uid_undo_restores_summary_snapshot(self):
        path=self.put('A.Name.2',data=archive(BAD));name=self.put('A.Name.2_2',data=fixtures.var_bytes('A.Name.2'));self.scan()
        self.c.label('a.name.2_2',summary='旧名称简介',source='manual')
        manager=PackageRepairs(self.c,self.ops,self.service().ai);p=manager.analyze(Job('preview'))
        selected=[{'type':i['type'],'id':i['id'],**({'filename':i['filename']} if i['type']=='name' else {})} for i in p['items']]
        result=manager.apply(p['id'],selected,Job('apply'))
        for op in reversed(result['operations']):self.ops.undo(op['operation'],Job('undo'))
        self.assertTrue(path.exists());self.assertTrue(name.exists());self.assertTrue(self.c.packages()['a.name.2_2']['summary_generated'])


class ValidationTests(TestCase):
    def test_syntax_examples(self):
        for old,new in [('{"a":1,}','{"a":1}'),('{"a":[1,]}','{"a":[1]}'),('{"a":1 "b":2}','{"a":1, "b":2}'),('{"a":1','{"a":1}'),('{"p":"Custom\\hair"}','{"p":"Custom\\\\hair"}')]:
            self.assertEqual(validate_edits(old,[{'before':old,'after':new}]),new)

    def test_compatible_quote_repair_keeps_existing_interpretation(self):
        from vampm.json_repair import compatible_edits
        from vampm.metadata import load_metadata
        text='{\n  "description": "Keep "quoted" text",\n  "dependencies": {}\n}'
        edits=compatible_edits(text);self.assertEqual(len(edits),1)
        fixed=validate_edits(text,edits)
        self.assertEqual(strict_json(fixed),load_metadata(text)[0])
        self.assertEqual(load_metadata(text,return_text=True)[2],fixed)

    def test_compatible_data_must_remain_identical(self):
        with self.assertRaisesRegex(ValueError,'兼容读取'):
            validate_edits('{"name":"Alice"',[{'before':'{"name":"Alice"','after':'{"name":"Bob"}'}])

    def test_unique_nonoverlapping_edits_and_strict_data(self):
        for edits in ([{'before':':','after':','}],[{'before':BASE,'after':'{}'}],[{'before':'xyz','after':''}], [{'before':BAD,'after':'{"a":1,"a":2}'}]):
            with self.assertRaises(ValueError):validate_edits(BAD,edits)
        for text in ('{"a":NaN}','[]','{"a":1,"a":2}'):
            with self.assertRaises(ValueError):strict_json(text)

class RevealTests(TestCase):
    def test_windows_shell_selects_full_path_and_releases_com_resources(self):
        import ctypes
        from pathlib import Path
        from vampm.reveal import reveal_file
        path=mock.Mock();path.resolve.return_value=path;path.is_file.return_value=True
        path.__str__=mock.Mock(return_value='G:\\中文, folder\\A.Look.1.var')
        shell=SimpleNamespace(SHParseDisplayName=mock.Mock(),SHOpenFolderAndSelectItems=mock.Mock(return_value=0))
        ole=SimpleNamespace(CoInitializeEx=mock.Mock(return_value=0),CoTaskMemFree=mock.Mock(),CoUninitialize=mock.Mock())
        def parse(name,ctx,pointer,*args):pointer._obj.value=1234;return 0
        shell.SHParseDisplayName.side_effect=parse
        with mock.patch('vampm.reveal.Path',return_value=path),mock.patch('vampm.reveal.os',SimpleNamespace(name='nt')),mock.patch('vampm.reveal.ctypes.WinDLL',side_effect=[shell,ole],create=True):
            self.assertEqual(reveal_file('test'),{'opened':True,'selected':True})
        self.assertEqual(shell.SHParseDisplayName.call_args.args[0],str(path))
        self.assertEqual(shell.SHOpenFolderAndSelectItems.call_args.args[0].value,1234)
        ole.CoTaskMemFree.assert_called_once();ole.CoUninitialize.assert_called_once()

    def test_api_selects_requested_copy_and_rejects_missing_key(self):
        import threading
        from pathlib import Path
        from vampm.server import Application
        rows=[{'id':1},{'id':2}]
        app=SimpleNamespace(closing=threading.Event(),agent=SimpleNamespace(busy=lambda:False),jobs=SimpleNamespace(busy=lambda:False),c=SimpleNamespace(packages=lambda:{'a.look.1':{'files':rows}},path=lambda row:Path('/library')/str(row['id'])))
        app.workspace_id='original'
        app.directories=SimpleNamespace(entry=lambda ident:{},available=lambda item:True)
        with mock.patch('vampm.server.reveal_file',return_value={'opened':True,'selected':True}) as reveal:
            Application._post(app,'/api/reveal',{'key':'a.look.1','file_id':2});reveal.assert_called_once_with(Path('/library/2'))
            with self.assertRaises(ValueError):Application._post(app,'/api/reveal',{'key':'a.look.1','file_id':3})
            with self.assertRaises(ValueError):Application._post(app,'/api/reveal',{'key':'missing'})
