import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import PurePosixPath

from .catalog import inspect_var
from .metadata import content_evidence, load_metadata
from .operations import Operations, move_no_replace
from .file_moves import receipt_path
from .util import (Cancelled, atomic_json, read_json, require_game_closed,
                   safe_path, sha256, split_id, stamp)

ASSET_EXTENSIONS = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.txt','.md','.pdf','.json'}
MAX_IMPORT_FILES = 10000
# Bounds each registration request, not the total size of an import session.


class Importer:
    def __init__(self,catalog,ai,downloads,operations=None):
        self.c,self.ai,self.downloads=catalog,ai,downloads
        self.ops=operations or Operations(catalog)
        # Do not delete legacy staging or recreate an offline game at startup.
        if self.c.storage.game_available() and self.c.storage.base.is_dir() and not self.c.storage.journal.exists():
            self.c.storage.ensure('imports')

    @property
    def staging(self):
        return self.c.storage.path('imports')

    def upload(self,stream,filename,length,relative_path=None):
        self.c.storage.ensure('imports')
        if not isinstance(filename,str) or '/' in filename or '\\' in filename:
            raise ValueError('文件名无效')
        package=filename.lower().endswith('.var')
        if package:
            if not split_id(filename) or not split_id(filename)[2].isdigit():
                raise ValueError('VAR 文件名需要符合 作者.名称.版本.var。')
        elif PurePosixPath(filename).suffix.lower() not in ASSET_EXTENSIONS or not relative_path:
            raise ValueError('仅支持 VAR，或文件夹中的常见图片、TXT、MD、PDF、JSON 附件。')
        safe_path(self.staging,filename)
        if relative_path:
            if not isinstance(relative_path,str) or len(relative_path)>1800:
                raise ValueError('文件夹内路径无效')
            safe_path(self.staging,relative_path)
            if '/' not in relative_path or relative_path.rsplit('/',1)[-1]!=filename:
                raise ValueError('文件夹路径必须包含所选文件夹名及原文件名')
            if any(any(c in part for c in '<>"|?*') or len(part)>255 for part in relative_path.split('/')):
                raise ValueError('文件夹路径包含 Windows 不支持的名称')
        limit=20*1024**3 if package else 512*1024**2
        if length<0 or (package and not length) or length>limit:
            raise ValueError('VAR 需要为非空文件且不超过 20 GiB；单个附件不超过 512 MiB。')
        if shutil.disk_usage(self.staging).free<length+256*1024**2: raise ValueError('磁盘剩余空间不足。')
        uid=uuid.uuid4().hex
        path=self.staging/(uid+('.var' if package else '.bin'))
        try:
            with path.open('xb') as f:
                remaining=length
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk: raise ValueError('文件上传中断')
                    f.write(chunk)
                    remaining-=len(chunk)
                f.flush()
                os.fsync(f.fileno())
            atomic_json(self.staging/(uid+'.json'),{'filename':filename,'size':length,'created':stamp(),
                                                   'relative_path':relative_path,'kind':'package' if package else 'asset'})
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {'id':uid,'filename':filename,'bytes':length}

    @staticmethod
    def validate_ids(ids):
        if not isinstance(ids,list) or not ids or len(ids)>MAX_IMPORT_FILES:
            raise ValueError('一次请选择 1–10,000 个符合条件的文件。')
        if any(not isinstance(uid,str) or len(uid)!=32 or any(c not in '0123456789abcdef' for c in uid) for uid in ids):
            raise ValueError('暂存文件标识无效')
        return list(dict.fromkeys(ids))

    def discard(self,ids):
        self.c.storage.check_pending()
        for uid in self.validate_ids(ids):
            for ext in ('.var','.bin','.json'):
                (self.staging/(uid+ext)).unlink(missing_ok=True)
        return {'discarded':len(ids)}

    def _session_path(self,session):
        if not isinstance(session,str) or len(session)!=32 or any(c not in '0123456789abcdef' for c in session):
            raise ValueError('导入会话标识无效。')
        return self.staging/('session-'+session+'.jsonl')

    def create_session(self):
        self.c.storage.ensure('imports')
        session=uuid.uuid4().hex
        self._session_path(session).touch(exist_ok=False)
        return {'session':session}

    def append_session(self,session,ids):
        ids=self.validate_ids(ids)
        path=self._session_path(session)
        # r+ never recreates an already consumed/cancelled session.
        with path.open('r+',encoding='utf-8') as f:
            f.seek(0,os.SEEK_END)
            f.write(json.dumps(ids)+'\n')
            f.flush()
            os.fsync(f.fileno())
        return {'registered':len(ids)}

    def _session_ids(self,session):
        ids=[]
        with self._session_path(session).open(encoding='utf-8') as f:
            for line in f:
                ids.extend(self.validate_ids(json.loads(line)))
        return list(dict.fromkeys(ids))

    def discard_session(self,session):
        self.c.storage.check_pending()
        path=self._session_path(session)
        if not path.exists():return {'discarded':0}
        ids=self._session_ids(session)
        for start in range(0,len(ids),MAX_IMPORT_FILES):
            self.discard(ids[start:start+MAX_IMPORT_FILES])
        path.unlink()
        return {'discarded':len(ids)}

    def run_session(self,session,job,use_ai=True,find=True):
        ids=self._session_ids(session)
        if not ids:raise ValueError('导入会话中没有文件。')
        try:
            return self._run(ids,job,use_ai,find)
        finally:
            self._session_path(session).unlink(missing_ok=True)

    def _folder_roots(self,metas):
        folders={}
        reserved=set()
        for root in (self.c.roots['active'],self.c.roots['vault']):
            base=safe_path(root,'包管家导入')
            if base.is_dir():reserved.update(p.name.casefold() for p in base.iterdir())
        for meta in metas:
            rel=meta.get('relative_path')
            if not rel:continue
            name=rel.split('/')[0]
            if name in folders:continue
            candidate=name;number=2
            while candidate.casefold() in reserved:
                candidate=name+'（'+str(number)+'）';number+=1
            reserved.add(candidate.casefold())
            folders[name]='包管家导入/'+candidate
        return folders

    def run(self,ids,job,use_ai=True,find=True):
        return self._run(self.validate_ids(ids),job,use_ai,find)

    def _run(self,ids,job,use_ai=True,find=True):
        result={'created':stamp(),'items':[],'installed':0,'attachments':0,'skipped':0,'failed':0,
                'missing':0,'ai_error':'','source_error':'','attachment_folders':[]}
        staged,assets,labels=[],[],{}
        installed = []
        retained = set()
        log=self.c.data/'import-history'/(uuid.uuid4().hex+'.json')
        log.parent.mkdir(exist_ok=True)
        try:
            require_game_closed()
            self.ops.check_pending()
            target=self.ops.import_target()
            area=target['area'];result.update(target)
            metas=[]
            for uid in ids:
                meta=read_json(self.staging/(uid+'.json'))
                if not meta:raise ValueError('导入暂存文件已不存在，请重新选择')
                metas.append(meta)
            folders=self._folder_roots(metas)
            existing_packages=self.c.packages()
            known_hashes={};batch_hashes={}
            for i,uid in enumerate(ids):
                job.check()
                meta=metas[i]
                asset=meta.get('kind')=='asset'
                path=self.staging/(uid+('.bin' if asset else '.var'))
                if not path.exists(): raise ValueError('导入暂存文件已不存在，请重新选择')
                name=meta['filename']
                relative=meta.get('relative_path')
                if relative:
                    root,tail=relative.split('/',1)
                    relative=folders[root]+'/'+tail
                job.update(i,len(ids),'校验 '+name)
                try:
                    if asset:
                        assets.append({'id':uid,'path':path,'filename':name,'relative':relative,'sha':sha256(path,job)})
                        continue
                    info=inspect_var(path,deep=True)
                    if info['error']: raise ValueError(info['error'])
                    warnings=[]
                    with zipfile.ZipFile(path) as z:
                        identity,_=load_metadata(z.read('meta.json'))
                        author_name,package_name,_=split_id(name)
                        normalize=lambda x:str(x).replace(' ','_').casefold()
                        if (normalize(identity.get('creatorName'))!=normalize(author_name)
                            or normalize(identity.get('packageName'))!=normalize(package_name)):
                            warnings.append('文件名与包内作者/名称不符；保留原始 VAR 文件名，使用时请核对内容。')
                        if sum(i.file_size for i in z.infolist())>60*1024**3: raise ValueError('包内解压体积异常')
                        for entry in z.infolist():
                            job.check()
                            if entry.is_dir(): continue
                            with z.open(entry) as f:
                                while f.read(2*1024*1024): job.check()
                    key=name[:-4].casefold()
                    existing=existing_packages.get(key)
                    digest=sha256(path,job)
                    if existing:
                        cache=(key,path.stat().st_size)
                        if cache not in known_hashes:
                            known_hashes[cache]={sha256(self.c.path(r),job) for r in existing['files'] if r['size']==cache[1]}
                        hashes=known_hashes[cache]
                        if digest in hashes:
                            result['items'].append({'filename':name,'status':'skipped','message':'本地已有相同内容'})
                            result['skipped']+=1
                            continue
                    other=batch_hashes.get(key,set())
                    if digest in other:
                        result['items'].append({'filename':name,'status':'skipped','message':'本批文件重复'})
                        result['skipped']+=1
                        continue
                    if existing or other:warnings.append('同名包内容不同，已保留为独立副本；可在启动方案中选择。')
                    author,pkgname,version=split_id(name)
                    staged.append({'id':uid,'path':path,'filename':name,'key':key,'sha':digest,'info':info,
                                   'author':author,'name':pkgname,'relative':relative,'warnings':warnings})
                    batch_hashes.setdefault(key,set()).add(digest)
                except Cancelled: raise
                except Exception as e:
                    result['failed']+=1
                    result['items'].append({'filename':name,'status':'error','message':str(e)[:400]})
            if use_ai and staged:
                try:
                    unique=list({x['key']:x for x in reversed(staged) if x['key'] not in existing_packages}.values())
                    for i in range(0,len(unique),10):
                        job.check()
                        job.update(i,len(staged),'AI 分析简介与分类')
                        batch=[dict(key=x['key'],name=x['name'],author=x['author'],counts=x['info']['counts'],_archive_path=x['path'],
                                    **content_evidence(x['path'])) for x in unique[i:i+10]]
                        for result_batch in self.ai.classification_chunks(batch,job):
                            labels.update(result_batch)
                except Cancelled: raise
                except Exception as e:
                    result['ai_error']=str(e)[:400]
            newkeys=[]
            checked = 0
            all_items=staged+assets
            for i,item in enumerate(all_items):
                job.check()
                if time.monotonic() - checked >= 1:
                    require_game_closed()
                    checked = time.monotonic()
                package='key' in item
                category=labels.get(item['key'],{}).get('category',item['info']['category']) if package else ''
                relative=item['relative'] or '包管家导入/'+category+'/'+item['filename']
                dest=safe_path(self.c.roots[area],relative)
                counter=0
                while dest.exists():
                    suffix=item['id'][:8]+('-'+str(counter) if counter else '')
                    dest=safe_path(self.c.roots[area],str(PurePosixPath(relative).parent)+'/导入副本/'+suffix+'/'+item['filename'])
                    counter+=1
                job.update(i,len(all_items),'加入资源库：'+item['filename'])
                try:
                    # Persist intent before the move; the original chosen file remains untouched.
                    result['pending']={'filename':item['filename'],'destination':str(dest),'sha256':item['sha']}
                    Operations._event(log.with_suffix('.jsonl'),phase='intent',**result['pending'])
                    try:
                        move_no_replace(item['path'],dest,job)
                    except BaseException:
                        if receipt_path(dest).exists():
                            retained.add(item['id'])
                            result.setdefault('retained_staging', []).append(
                                {'id': item['id'], 'path': str(item['path']), 'destination': str(dest)})
                        raise
                    if package:
                        installed.append({'area':area,'rel':dest.relative_to(self.c.roots[area]).as_posix(),
                                          'info':item['info'],'sha':item['sha'],'warnings':item['warnings']})
                    result.pop('pending',None)
                    if package:
                        result['installed']+=1
                        newkeys.append(item['key'])
                    else:
                        result['attachments']+=1
                        if str(dest.parent) not in result['attachment_folders']:result['attachment_folders'].append(str(dest.parent))
                    result['items'].append({'filename':item['filename'],'status':'installed',
                                            'message':('已加入 / '+category if package else '附件已复制')+(' · 停用库' if area=='vault' else ' · 启用库'),
                                            'warnings':item.get('warnings',[]),'destination':str(dest),'sha256':item['sha']})
                    if (i+1)%64==0 or i+1==len(all_items):atomic_json(log,result)
                except Exception as e:
                    result.pop('pending',None)
                    result['failed']+=1
                    result['items'].append({'filename':item['filename'],'status':'error','message':str(e)[:400]})
            self.c.index_files(installed)
            installed.clear()
            for key in newkeys:
                if key in labels: self.c.label(key,**labels[key],source='ai')
            result['missing']=len(self.c.missing(newkeys)) if newkeys else 0
            if find and newkeys and result['missing']:
                try: self.downloads.find(job,newkeys)
                except Cancelled: raise
                except Exception as e: result['source_error']=str(e)[:400]
            job.update(len(ids),len(ids),'导入完成')
            return result
        finally:
            atomic_json(log,result)
            self.c.index_files(installed)
            disposable = [uid for uid in ids if uid not in retained]
            for start in range(0,len(disposable),MAX_IMPORT_FILES):
                self.discard(disposable[start:start+MAX_IMPORT_FILES])
