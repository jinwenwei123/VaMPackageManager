import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from .ai_response import AIResponseError, completion_json, decode_json
from .ai_usage import APIUsage
from .chat_context import packed
from .request_context import ContextPolicy, RequestMeter, context_overflow, generation_system
from .catalog import inspect_var
from .metadata import content_evidence, load_metadata
from .operations import move_no_replace
from .file_moves import receipt_path
from .sources import Sources
from .content_tags import instruction as content_instruction, normalize_tags, needed, outputs
from .summary_content import INSTRUCTION, concise_summary, prepare_inputs
from .language import output_instruction
from .util import (CATEGORIES, Cancelled, Job, atomic_json, fingerprint, hidden_process_args, matches,
                   protect_secret, read_json, require_game_closed, safe_path, split_id,
                   stamp, unprotect_secret, network_process_env, stop_process, cancellable_read)

HUB = 'https://hub.virtamate.com/citizenx/api.php'
REPOSITORIES = {'acidbubbles.timeline':'acidbubbles/vam-timeline',
                'acidbubbles.embody':'acidbubbles/vam-embody'}

# Package management goes through our explicit APIs, including for current Codex
# versions that also ship browser/computer/automation capabilities.
CODEX_DISABLED_FEATURES = ('shell_tool','apps','multi_agent','multi_agent_v2','plugins',
    'skill_mcp_dependency_install','skill_search','hooks','browser_use','browser_use_external',
    'computer_use','image_generation','view_image','goals','sleep_tool','code_mode',
    'in_app_browser','in_app_local_automation','workspace_dependencies','worktrees','remote_plugin')


def approved_url(url,extra_hosts=()):
    try:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or '').lower()
        return (u.scheme=='https' and not u.username and not u.password and u.port in (None,443)
            and (host in {'hub.virtamate.com','github.com','api.github.com',
                          'release-assets.githubusercontent.com','objects.githubusercontent.com'}
                 or host.endswith('.virtamate.com') or host.endswith('.rsc.cdn77.org')
                 or host in extra_hosts))
    except ValueError:return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def trusted_open(url, payload=None, timeout=45, extra_hosts=()):
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(6):
        if not approved_url(url,extra_hosts):
            raise ValueError('下载链接不在可信来源范围内：'+urllib.parse.urlsplit(url).netloc)
        headers = {'User-Agent':'VaMPackageManager/1.2', 'Accept':'application/json, */*'}
        if urllib.parse.urlsplit(url).hostname=='hub.virtamate.com': headers['Cookie']='vamhubconsent=yes'
        data = json.dumps(payload).encode() if payload is not None else None
        if data is not None: headers['Content-Type']='application/json'
        try:
            return opener.open(urllib.request.Request(url,data=data,headers=headers),timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (301,302,303,307,308): raise
            url = urllib.parse.urljoin(url,e.headers.get('Location',''))
            if e.code in (301,302,303): payload=None
            e.close()
    raise ValueError('下载重定向次数过多')


def trusted_json(url, payload=None):
    with trusted_open(url,payload) as r:
        b = r.read(16 * 1024 * 1024 + 1)
        if len(b)>16*1024*1024: raise ValueError('服务器响应过大')
        return json.loads(b.decode('utf-8-sig'))


class Downloads:
    def __init__(self, catalog):
        self.c = catalog
        self.report = self.c.data/'dependency-sources.json'
        self.sources = Sources(self.c)
        self.last = self.c.data/'last-download.json'

    def get(self):
        report=read_json(self.report,{'ready':[],'unresolved':[],'created':None})
        current=self.c.missing(report.get('scope') or None)
        missing={x['ref'].casefold():x for x in current}
        ready=[]
        retained={}
        local=self.c.packages()
        for item in report['ready']:
            refs=[r for r in item['refs'] if r.casefold() in missing]
            existing=local.get(item['filename'][:-4].casefold())
            if existing and any('文件已保留' in w and ('元数据无法读取' in w or '包内容无法完整读取' in w)
                                for w in existing.get('warnings',[])):
                for ref in refs:
                    retained[ref.casefold()]='文件已下载保留；包读取异常，可在资源库查看感叹号详情，不重复下载或覆盖'
                continue
            if refs:ready.append(dict(item,refs=refs))
        found={r.casefold() for x in ready for r in x['refs']}
        old={x['ref'].casefold():x for x in report['unresolved']}
        report['ready']=ready
        report['unresolved']=[dict(x,reason=retained.get(x['ref'].casefold()) or old.get(x['ref'].casefold(),{}).get('reason','新增或尚未查询来源的依赖'))
                              for x in current if x['ref'].casefold() not in found]
        report['searched_missing_count']=report.get('missing_count',0)
        report['missing_count']=len(current)
        report['global_missing_count']=len(self.c.missing())
        report['last_install']=read_json(self.last)
        if report['last_install'] is None:
            paths=sorted((self.c.data/'download-history').glob('*.json'),reverse=True)
            if paths: report['last_install']=read_json(paths[0])
        return report

    def find(self, job, keys=None):
        missing = self.c.missing(keys)
        results, problems = {}, []
        for i in range(0,len(missing),40):
            job.check()
            batch = missing[i:i+40]
            job.update(i,len(missing),'正在查询 VaM Hub 官方目录')
            try:
                data = trusted_json(HUB,{'source':'VaM','action':'findPackages','packages':','.join(x['ref'] for x in batch)})
                if not isinstance(data.get('packages'),dict): raise ValueError('Hub 返回格式无法识别')
                results.update({k.casefold():v for k,v in data['packages'].items()})
            except Exception as e:
                problems.append(str(e)[:220])
        ready, unresolved, github_cache = {}, [], {}
        repositories=self.sources.repositories(REPOSITORIES)
        external=self.sources.external(missing,job,problems)
        for i,item in enumerate(missing):
            job.check()
            ref = item['ref']
            hit = results.get(ref.casefold())
            candidate = None
            if isinstance(hit,dict):
                filename = str(hit.get('filename',''))
                url = str(hit.get('downloadUrl',''))
                if filename.lower().endswith('.var') and matches(ref,filename) and approved_url(url):
                    try: size = int(hit.get('file_size') or 0)
                    except (ValueError,TypeError): size = 0
                    candidate = {'filename':filename,'url':url,'source':'VaM Hub 官方','bytes':size}
            if candidate is None:
                family=ref.rsplit('.',1)[0].casefold()
                repos=repositories.get(family,[])+repositories.get(ref.split('.',1)[0].casefold()+'.*',[])
                for repo in dict.fromkeys(repos):
                    if repo not in github_cache:
                        try:
                            github_cache[repo]=self.sources.releases(repo,job)
                        except Cancelled: raise
                        except Exception as e:
                            github_cache[repo]=[]
                            problems.append('GitHub '+repo+': '+str(e)[:160])
                    hits=[a for a in github_cache[repo] if a.get('name','').lower().endswith('.var')
                          and matches(ref,a['name']) and approved_url(a.get('browser_download_url',''))]
                    if hits:
                        hits.sort(key=lambda a:int(split_id(a['name'])[2]),reverse=True)
                        a=hits[0]
                        candidate={'filename':a['name'],'url':a['browser_download_url'],'source':'作者声明 GitHub',
                                   'bytes':a.get('size',0),'page_url':'https://github.com/'+repo,
                                   'digest':a.get('digest','')}
                        break
            if candidate is None:
                hits=[x for x in external if ref in x['refs']]
                if hits:
                    hits.sort(key=lambda x:int(split_id(x['filename'])[2]),reverse=True)
                    candidate={k:v for k,v in hits[0].items() if k!='refs'}
            if candidate:
                entry=ready.setdefault(candidate['filename'].casefold(),dict(candidate,refs=[]))
                entry['refs'].append(ref)
            else:
                unresolved.append({**item,'reason':'已查询的来源无匹配版本，或本次网络查询失败',
                    'search':'https://hub.virtamate.com/search/?q='+urllib.parse.quote(ref.rsplit('.',1)[0])})
            job.update(i+1,len(missing),'核对来源与版本：'+ref)
        report={'created':stamp(),'ready':list(ready.values()),'unresolved':unresolved,
                'errors':list(dict.fromkeys(problems)),'scope':keys or [],'missing_count':len(missing)}
        atomic_json(self.report,report)
        current=self.get()
        return {'available':len(current['ready']),'unresolved':len(current['unresolved']),'network_errors':len(problems)}

    def download_one(self, item, job):
        filename=item['filename']
        if '/' in filename or '\\' in filename or not split_id(filename) or not filename.lower().endswith('.var'):
            raise ValueError('远程文件名无效')
        target=safe_path(self.c.roots['active'],'包管家补充/'+filename)
        if target.exists(): raise ValueError('目标文件已存在，请重新扫描')
        if item['refs'] and all(self.c.resolver()(r) for r in item['refs']):
            return {'filename':filename,'status':'skipped','reason':'本地已有可用版本'}
        folder=self.c.storage.ensure('downloads')
        part=folder/(uuid.uuid4().hex+'.part')
        digest=hashlib.sha256()
        downloaded=0
        warnings=[]
        try:
            hosts=self.sources.hosts(item.get('source_id'))
            with trusted_open(item['url'],timeout=60,extra_hosts=hosts) as response, part.open('xb') as out:
                length=int(response.headers.get('Content-Length') or 0)
                disposition=response.headers.get('Content-Disposition','')
                named=re.search(r"filename\*\s*=\s*UTF-8''([^;]+)",disposition,re.I)
                if not named: named=re.search(r'filename\s*=\s*"?([^";]+)',disposition,re.I)
                server_name=urllib.parse.unquote(named[1].strip()) if named else ''
                if server_name.lower().endswith('.var') and server_name.casefold()!=filename.casefold():
                    warnings.append('下载服务器文件名「'+server_name[:240]+'」与发布文件名「'+filename+'」不一致；按发布文件名保留，需在 VaM 中确认可用性')
                # VAR assets are bounded; HTML/error responses are rejected by ZIP validation below.
                limit=20*1024**3
                if length>limit: raise ValueError('单包超过 20 GiB，需手动下载确认')
                while chunk:=response.read(1024*1024):
                    job.check()
                    downloaded+=len(chunk)
                    if downloaded>limit: raise ValueError('下载超出大小限制')
                    out.write(chunk)
                    digest.update(chunk)
                    job.message=f'下载 {filename} · {downloaded/1024**2:.1f} / {length/1024**2:.1f} MiB'
                out.flush()
                os.fsync(out.fileno())
                if length and downloaded!=length: raise ValueError('下载不完整')
            if item.get('bytes') and downloaded!=item['bytes']:
                warnings.append('发布页体积 '+str(item['bytes'])+' 与实际 '+str(downloaded)+' 不同；已完整接收并通过 ZIP CRC 检查')
            expected=item.get('digest','') or ''
            if expected.startswith('sha256:') and digest.hexdigest()!=expected[7:]:
                warnings.append('文件 SHA-256 与发布信息不符；发布值 '+expected[7:]+'，实际值 '+digest.hexdigest()+'；已保留，文件是否为原发布版本尚未确认')
            with zipfile.ZipFile(part) as z:
                if sum(i.file_size for i in z.infolist())>60*1024**3: raise ValueError('解压体积异常')
                for zi in z.infolist():
                    job.check()
                    if zi.is_dir(): continue
                    with z.open(zi) as f:
                        while f.read(2*1024*1024): job.check()
                mi=next((i for i in z.infolist() if i.filename.lower()=='meta.json'),None)
                meta=None
                try:
                    if not mi or mi.file_size>8*1024*1024: raise ValueError('缺少 meta.json 或元数据过大')
                    meta,meta_warnings=load_metadata(z.read(mi));warnings.extend(meta_warnings)
                except (ValueError,UnicodeError) as e:
                    warnings.append('包内元数据无法读取：'+str(e)[:350]+'；文件已保留，可用性待确认')
                parsed=split_id(filename)
                normalize=lambda v:re.sub(r'[ _]+','_',str(v)).casefold()
                if meta is not None and normalize(meta.get('creatorName'))!=normalize(parsed[0]):
                    warnings.append('包内作者「'+str(meta.get('creatorName') or '未填写')[:240]+'」与发布文件名作者「'+parsed[0]+'」不一致；保留原文件内容')
                if meta is not None and normalize(meta.get('packageName'))!=normalize(parsed[1]):
                    warnings.append('包内名称「'+str(meta.get('packageName') or '未填写')[:240]+'」与发布文件名包名「'+parsed[1]+'」不一致；保留原文件内容')
            info=inspect_var(part)
            if info['error'] and not any('元数据无法读取' in w for w in warnings):
                warnings.append('包内容无法完整读取：'+info['error']+'；文件已保留，可用性待确认')
            job.check()
            require_game_closed()
            move_no_replace(part,target,job)
            self.c.index_files([{'area':'active','rel':target.relative_to(self.c.roots['active']).as_posix(),
                                 'info':info,'sha':digest.hexdigest(),'warnings':warnings}])
            return {'filename':filename,'status':'installed','bytes':downloaded,'sha256':digest.hexdigest(),
                    'url':item['url'],'path':str(target),'warnings':warnings,'package_error':info['error']}
        finally:
            # A published cross-volume copy may still need source cleanup.
            # Keep its source and receipt for recovery instead of deleting evidence.
            if part.exists() and not receipt_path(target).exists(): part.unlink()

    def install(self, job, names=None):
        require_game_closed()
        self.c.scan(job)
        report=self.get()
        selected=set(names or [])
        items=[x for x in report['ready'] if not selected or x['filename'] in selected]
        before={r['ref'].casefold() for r in self.c.missing()}
        log={'created':stamp(),'items':[],'missing_before':len(before)}
        path=self.c.data/'download-history'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.json')
        try:
            for i,item in enumerate(items):
                job.check()
                job.update(i,len(items),item['filename'])
                try: result=self.download_one(item,job)
                except Cancelled: raise
                except Exception as e: result={'filename':item['filename'],'status':'error','error':str(e)[:500]}
                log['items'].append(result)
                atomic_json(path,log)
            job.update(len(items),len(items),'下载完成')
        finally:
            after={r['ref'].casefold() for r in self.c.missing()}
            log.update(installed=sum(x['status']=='installed' for x in log['items']),
                       warned=sum(bool(x.get('warnings')) for x in log['items']),
                       unreadable=sum(bool(x.get('package_error')) for x in log['items']),
                       skipped=sum(x['status']=='skipped' for x in log['items']),
                       failed=sum(x['status']=='error' for x in log['items']),
                       resolved=len(before-after),new_missing=len(after-before),remaining_missing=len(after))
            atomic_json(path,log)
            atomic_json(self.last,log)
        return log


def find_codex(configured=''):
    if configured:
        p=Path(configured)
        if p.is_file() and p.suffix.lower() not in ('.cmd','.bat'):
            return str(p)
        raise ValueError('请指定 codex.exe 的完整路径，而不是 CMD/BAT 文件。')
    if os.name=='nt':
        bundled=Path(__file__).resolve().parents[2]/'runtime/codex/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        if bundled.is_file(): return str(bundled)
        base=Path(os.environ.get('APPDATA',''))/'npm/node_modules/@openai/codex/node_modules'
        found=list(base.glob('@openai/codex-win32-*/vendor/*/bin/codex.exe'))
        found+=list(base.glob('@openai/codex-win32-*/vendor/*/codex/codex.exe'))
        if found: return str(found[0])
    exe=shutil.which('codex.exe') or shutil.which('codex')
    if exe and not exe.lower().endswith(('.cmd','.bat')): return exe
    raise ValueError('未找到 Codex CLI。请安装官方 Codex CLI 并登录，或填写 codex.exe 路径。')


class AI:
    def __init__(self,catalog,language=None):
        self.c=catalog
        self.language=language
        self.file=self.c.data/'ai-settings.json'
        self.process_lock=threading.Lock()
        self.processes=[]
        self.closed=False
        self.usage=APIUsage(self.c.data)
        from .model_catalog import ModelCatalog
        self.models=ModelCatalog(self)

    def spawn(self,*args,**kwargs):
        with self.process_lock:
            if self.closed: raise Cancelled('包管家正在退出。')
            self.processes=[p for p in self.processes if p.poll() is None]
            process=subprocess.Popen(*args,**kwargs)
            self.processes.append(process)
            return process

    def close(self):
        self.models.close()
        with self.process_lock:
            self.closed=True
            processes=self.processes;self.processes=[]
        for process in processes: stop_process(process)

    def settings(self,private=False):
        from .model_catalog import LEGACY_EFFORT_FIELDS
        data={'provider':'codex','codex_path':'','codex_model':'gpt-6.1-sol','codex_summary_model':'gpt-6-luna',
              'base_url':'https://api.deepseek.com','api_model':'deepseek-flash','secret':'',
              'summary_images':True,'api_context_window':0}
        data.update(read_json(self.file,{}))
        for field in LEGACY_EFFORT_FIELDS: data.pop(field,None)
        # Old installations used one API model for both purposes. Only reading
        # settings must not rewrite credentials or change the user's selection.
        data.setdefault('api_summary_model',data['api_model'])
        data.pop('api_vision',None)
        if private:
            data['_output_language']=self.language.get()['language'] if self.language else 'zh-CN'
            return data
        from .api_profiles import state, connection
        active,profiles=state(data)
        data['api_profiles']={name:self.public_connection(connection(data,name)) for name in profiles}
        data['api_profile']=active
        data.pop('api_connections',None)
        data.update(self.public_connection(data))
        data.pop('secret',None)
        data.pop('model_capability_overrides',None)
        return data

    def public_connection(self,data):
        from .model_capabilities import overrides
        from .api_profiles import FIELDS
        return {**{key:data[key] for key in FIELDS if key!='secret'},
                'api_capability_overrides':overrides(data),
                'api_model_capabilities':{m:self.models.known(data,m) for m in {data['api_model'],data['api_summary_model']}},
                'has_key':bool(data.get('secret') or os.environ.get('VAMPM_API_KEY'))}

    def connection(self,service):
        from .api_profiles import connection
        return connection(self.settings(True),service)

    def save(self,data):
        from .api_profiles import prepare, remember
        old=prepare(self.settings(True),data)
        for k in ('provider','codex_path','codex_model','codex_summary_model','base_url','api_model','api_summary_model'):
            if k in data: old[k]=str(data[k]).strip()[:2000]
        for k in ('summary_images',):
            if k in data:
                if not isinstance(data[k],bool): raise ValueError('图片分析选项须为布尔值')
                old[k]=data[k]
        if 'api_context_window' in data:
            value=data['api_context_window']
            if isinstance(value,str) and value.isdecimal():value=int(value)
            if type(value) is not int or value!=0 and not 8192<=value<=4000000:
                raise ValueError('API 上下文容量须为0（自动）或8192–4000000 tokens')
            old['api_context_window']=value
        if old['provider'] not in ('codex','deepseek','compatible'): raise ValueError('AI 服务类型无效')
        if any(not old[k] for k in ('codex_model','codex_summary_model','api_model','api_summary_model')): raise ValueError('模型名不能为空')
        self.api_url(old)
        if 'api_capability_overrides' in data:
            from .model_capabilities import scope, validate_overrides
            selected=scope(old['base_url'])
            entries=validate_overrides(data['api_capability_overrides'])
            saved=dict(old.get('model_capability_overrides',{}))
            if entries: saved[selected]=entries
            else: saved.pop(selected,None)
            if len(saved)>100: raise ValueError('模型能力设置最多保存100个接口。')
            old['model_capability_overrides']=saved
        if data.get('clear_key'): old['secret']=''
        if data.get('api_key'): old['secret']=protect_secret(data['api_key'])
        remember(old)
        old.pop('_output_language',None)
        atomic_json(self.file,old)
        try: os.chmod(self.file,0o600)
        except OSError: pass
        return self.settings()

    @staticmethod
    def api_url(config):
        base=config['base_url'].rstrip('/')
        u=urllib.parse.urlsplit(base)
        if u.username or u.password or u.query or u.fragment or not u.hostname:
            raise ValueError('API 地址格式无效')
        if u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost','::1')):
            raise ValueError('API 地址必须是 HTTPS；本机服务可使用 HTTP。')
        return base if base.endswith('/chat/completions') else base+'/chat/completions'

    def status(self):
        cfg=self.settings(True)
        try:
            exe=find_codex(cfg['codex_path'])
            p=self.spawn([exe,'login','status'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         encoding='utf-8',errors='replace',**hidden_process_args())
            try: stdout,stderr=p.communicate(timeout=20)
            except BaseException:
                stop_process(p)
                raise
            msg=(stdout+' '+stderr).strip()
            return {'ok':p.returncode==0,'path':exe,'message':msg[:600]}
        except Exception as e: return {'ok':False,'message':str(e)}

    def login(self):
        exe=find_codex(self.settings(True)['codex_path'])
        flags={'creationflags':subprocess.CREATE_NEW_CONSOLE} if os.name=='nt' else {}
        self.spawn([exe,'login'],**flags)
        return {'opened':True}

    @staticmethod
    def validate(data, keys, target="summary", language="zh-CN"):
        if not isinstance(data,dict) or not isinstance(data.get('items'),list): raise AIResponseError('AI 未返回正确的 JSON 分类结果')
        results={}
        for item in data['items']:
            if not isinstance(item,dict): raise AIResponseError('AI 结果项目无效')
            key=str(item.get('key','')).casefold()
            if key not in keys or key in results: raise AIResponseError('AI 返回了未知或重复包名')
            label={}
            if target in ('summary','both'):
                if item.get('category') not in CATEGORIES: raise AIResponseError('AI 返回了无效分类')
                desc=concise_summary(item.get('summary',''),item['category'])
                if not isinstance(desc,str) or not desc.strip() or not re.search('[A-Za-z]' if language=='en' else '[\u4e00-\u9fff]',desc):
                    raise AIResponseError('AI summary must contain non-empty English text' if language=='en' else 'AI 简介须为非空的中文说明')
                label.update(category=item['category'],summary=desc.strip())
            if target in ('tags','both'):
                try:
                    tags=item.get('tags')
                    if language=='en' and isinstance(tags,list):
                        tags=['Adult themes' if isinstance(tag,str) and tag.casefold() in ('r18','r-18','成人向') else tag for tag in tags]
                    label['tags']=normalize_tags(tags)
                except ValueError as e:raise AIResponseError(str(e)) from None
            results[key]=label
        if set(results)!=set(keys): raise AIResponseError('AI 返回结果不完整；该批没有写入')
        return results

    def classification_chunks(self,items,job,target="summary"):
        """Yield validated sub-batches so saved work survives a later failure."""
        size=10 if self.settings(True)['provider']=='codex' else 5
        retries=6
        def run(batch,recovery=False,single_retry=False):
            nonlocal retries
            job.check()
            if recovery:
                retries-=1
            try:
                result=self.classify_batch(batch,job) if target=="summary" else self.classify_batch(batch,job,target=target)
            except AIResponseError as error:
                job.check()
                if error.details.get('stage')=='context_overflow' and len(batch)==1:
                    raise AIResponseError('单包资料超过模型上下文容量，请更换简介模型或核对模型容量设置。',
                                          **error.details) from None
                if retries<=0 or single_retry:
                    raise AIResponseError(str(error)+' 自动恢复已停止；已完成简介保留，可点击继续或更换简介模型。',
                                          **error.details) from None
                job.message=f'AI 回复不完整或格式异常，正在缩小批次重试（本批 {len(batch)} 个包）'
                if len(batch)>1:
                    middle=len(batch)//2
                    yield from run(batch[:middle],True)
                    if retries<=0:
                        raise AIResponseError('自动恢复次数已用完；已完成简介保留，点击继续生成剩余资源。')
                    yield from run(batch[middle:],True)
                else:
                    yield from run(batch,True,True)
                return
            yield result
        for i in range(0,len(items),size):
            yield from run(items[i:i+size])

    def classify_batch(self,items,job,target="summary"):
        cfg=self.settings(True)
        cfg['_usage_purpose']=target
        if not hasattr(job,'output_language'):job.output_language=cfg.get('_output_language','zh-CN')
        cfg['_output_language']=job.output_language
        outputs(target)
        instruction=output_instruction(content_instruction(target,INSTRUCTION),cfg.get('_output_language'))
        originals=items
        use_images=cfg['summary_images'] and any(x.get('_archive_path') for x in items) and self.models.supports_images(cfg,job.cancel)
        items,images=prepare_inputs(items,use_images,job)
        # Base64 expands by 4/3. Keep below DeepSeek's 48 MiB request limit,
        # splitting rather than silently dropping previews from large batches.
        if sum(len(x['data']) for x in images)>30*1024*1024:
            middle=len(originals)//2
            return {**self.classify_batch(originals[:middle],job,target),**self.classify_batch(originals[middle:],job,target)}
        properties={'key':{'type':'string'}}
        if target in ('summary','both'):properties.update(category={'type':'string','enum':CATEGORIES},summary={'type':'string'})
        if target in ('tags','both'):properties['tags']={'type':'array','items':{'type':'string'},'maxItems':24}
        schema={'type':'object','properties':{'items':{'type':'array','items':{'type':'object','properties':properties,
            'required':list(properties),'additionalProperties':False}}},'required':['items'],'additionalProperties':False}
        model=cfg['codex_summary_model'] if cfg['provider']=='codex' else cfg['api_summary_model']
        if cfg['provider']!='codex':
            system=generation_system(instruction,packed(schema))
            meter=RequestMeter(cfg,model,system)
            content=[{'type':'text','text':packed(items)}]+[{'type':'image_url'} for _ in images]
            output=8192 if (urllib.parse.urlsplit(cfg['base_url']).hostname=='api.deepseek.com'
                            and model in ('deepseek-flash','deepseek-v4-pro')) else 4096
            budget=ContextPolicy(self.models.context_window(cfg,model),output)
            if meter.measure([{'role':'user','content':content}])>=budget.threshold:
                if len(originals)==1:raise ValueError('单包资料超过模型输入预算，请使用更大上下文的简介模型。')
                middle=len(originals)//2
                return {**self.classify_batch(originals[:middle],job,target),
                        **self.classify_batch(originals[middle:],job,target)}
        answer=self.complete_json(cfg,instruction,items,schema,job,model,images)
        return self.validate(answer,{x['key'] for x in items},target,cfg.get('_output_language','zh-CN'))

    def complete_json(self,cfg,instruction,items,schema,job,model,images=(),output_limit=None):
        """Isolated JSON generation; no package tools or conversation mutations."""
        try:
            return self._complete_json(cfg,instruction,items,schema,job,model,images,output_limit)
        except AIResponseError as error:
            # Keep only bounded diagnostics, never the reply, images or secret.
            report={'created':stamp(),'provider':cfg['provider'],'model':model,
                    'items':len(items) if isinstance(items,list) else 1,**error.details}
            try: atomic_json(self.c.data/'ai-response-diagnostic.json',report)
            except OSError: pass
            raise

    def _complete_json(self,cfg,instruction,items,schema,job,model,images,output_limit=None):
        job.check()
        instruction=output_instruction(instruction,cfg.get('_output_language','zh-CN'))
        effort=self.models.reasoning(cfg,model,job.cancel)
        prompt=instruction+'\n待处理的输入：\n'+json.dumps(items,ensure_ascii=False)
        if cfg['provider']=='codex':
            exe=find_codex(cfg['codex_path'])
            work=self.c.data/'ai-work'
            work.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=work) as td:
                folder=Path(td)
                atomic_json(folder/'schema.json',schema)
                attachments=[]
                for image in images:
                    job.check()
                    target=folder/image['filename'];target.write_bytes(image['data'])
                    attachments+=['--image',str(target)]
                command=[exe,'exec',*attachments,'--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check',
                    '-s','read-only','-c','approval_policy="never"',
                    '-c','web_search="disabled"','-c','project_doc_max_bytes=0',
                    '-m',model,'-C',str(folder),'--color','never','--output-schema',str(folder/'schema.json'),
                    '-o',str(folder/'result.json')]
                if effort is not None: command+=['-c','model_reasoning_effort='+json.dumps(effort)]
                for feature in CODEX_DISABLED_FEATURES: command+=['-c','features.'+feature+'=false']
                command+=['-']
                # File-backed streams avoid deadlocks from a full subprocess stderr pipe.
                (folder/'input.txt').write_text(prompt,'utf-8')
                with (folder/'input.txt').open('rb') as inp, (folder/'output.log').open('wb') as out:
                    p=self.spawn(command,stdin=inp,stdout=out,stderr=out,env=network_process_env(),**hidden_process_args())
                    start=time.monotonic()
                    try:
                        while p.poll() is None:
                            job.check()
                            log_text=(folder/'output.log').read_text('utf-8',errors='replace')
                            if ('Your access token could not be refreshed' in log_text
                                or 'unauthorized_unknown' in log_text):
                                raise ValueError('Codex 登录凭据已失效，请在「AI 与设置」点击「登录 Codex」重新登录后再试。')
                            if time.monotonic()-start>180: raise TimeoutError('Codex 请求超过 180 秒；请检查登录、模型权限与网络。')
                            time.sleep(.25)
                    except BaseException:
                        stop_process(p)
                        raise
                job.check()
                result=folder/'result.json'
                if p.returncode or not result.is_file():
                    log=(folder/'output.log').read_text('utf-8',errors='replace')
                    diagnostic=[line for line in log.splitlines() if re.search(r'(?i)(error:|error\]|failed:|not supported|unauthorized)',line)]
                    message=' '.join(diagnostic[-2:])[-900:]
                    if 'Your access token could not be refreshed' in log or 'unauthorized_unknown' in log:
                        raise ValueError('Codex 登录凭据已失效，请在「AI 与设置」点击「登录 Codex」重新登录后再试。')
                    message=re.sub(r'(?:sk-[\w-]+|eyJ[\w.-]+)', '[已隐藏]',message)
                    message=re.sub(r'(?i)(bearer|authorization|api[_-]?key)\s*[:= ]+\S+',r'\1 [已隐藏]',message)
                    raise ValueError('Codex 调用失败。'+(message or '请检查 CLI 登录、模型是否可用、额度与网络。'))
                answer=decode_json(result.read_text('utf-8-sig'),stage='codex')
        else:
            key=unprotect_secret(cfg['secret'])
            if not key: raise ValueError('请先填写 API 密钥。')
            url=self.api_url(cfg)
            content=packed(items)
            if images:
                content=[{'type':'text','text':content}]
                for index,image in enumerate(images,1):
                    job.check()
                    content.extend([{'type':'text','text':f"附件 {index} 对应包 {image['key']}"},
                        {'type':'image_url','image_url':{'url':'data:'+image['mime']+';base64,'+base64.b64encode(image['data']).decode('ascii'),'detail':'auto'}}])
            # A deterministic, useful shared prefix; variable package data/images
            # stay in the final user message. Preserve the user's instruction text.
            system=generation_system(instruction,packed(schema))
            payload={'model':model,'messages':[{'role':'system','content':system},
                {'role':'user','content':content}],
                'response_format':{'type':'json_object'},'max_tokens':4096}
            if effort is not None: payload['reasoning_effort']=effort
            # Keep output headroom from the truncation fix independently of effort.
            if (isinstance(items,list) and urllib.parse.urlsplit(url).hostname=='api.deepseek.com'
                and model in ('deepseek-flash','deepseek-v4-pro')):
                payload['max_tokens']=8192
            if output_limit is not None:
                if type(output_limit) is not int or not 4096<=output_limit<=16384:raise ValueError('JSON 输出上限无效')
                payload['max_tokens']=output_limit
            meter=RequestMeter(cfg,model,system)
            window=self.models.context_window(cfg,model)
            if meter.measure(payload['messages'][1:])+payload['max_tokens']>window:
                raise ValueError('生成资料与输出预留超过模型上下文容量，请使用更大上下文的模型。')
            req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={
                'Content-Type':'application/json','Authorization':'Bearer '+key})
            # Never forward the API key to a redirect target.
            opener=urllib.request.build_opener(NoRedirect())
            def read_response():
                with opener.open(req,timeout=90) as r: return r.read(4*1024*1024+1)
            try:
                raw=cancellable_read(read_response,job.cancel)
            except urllib.error.HTTPError as e:
                overflow=context_overflow(e)
                e.close()
                if overflow:
                    raise AIResponseError('模型报告生成资料超出上下文；将缩小批次，单包内容不作摘要。',stage='context_overflow') from None
                hint='；模型声明支持图片，但接口未接受本次输入，请刷新模型列表或关闭“参考包内封面”后重试。' if images and e.code in (400,415,422) else '；请核对地址、模型、密钥和额度。'
                raise ValueError(f'API 返回 HTTP {e.code}'+hint) from None
            if len(raw)>4*1024*1024:
                raise AIResponseError('API 回复超过接收上限；该回复未保存。',stage='envelope')
            self.usage.record(cfg,model,cfg.get('_usage_purpose','generation'),raw)
            try: answer=completion_json(raw)
            except AIResponseError as error:
                error.details.update(max_tokens=payload['max_tokens'],response_bytes=len(raw))
                raise
        job.check()
        return answer

    def draft_prompt(self,title,prompt,job):
        from .chat_presets import PROMPT_INSTRUCTION, text_field
        title=text_field(title,'标题',60)
        prompt=text_field(prompt,'原提示词',12000,required=False)
        cfg=self.settings(True)
        cfg['_usage_purpose']='preset'
        model=cfg['codex_model'] if cfg['provider']=='codex' else cfg['api_model']
        schema={'type':'object','properties':{'prompt':{'type':'string'}},
                'required':['prompt'],'additionalProperties':False}
        job.update(0,1,'正在使用对话模型编写提示词…')
        answer=self.complete_json(cfg,PROMPT_INSTRUCTION,{'title':title,'prompt':prompt},schema,job,model)
        if not isinstance(answer,dict): raise ValueError('AI 未返回有效的提示词')
        result=text_field(answer.get('prompt'),'AI 提示词',12000)
        return {'preset_prompt':result,'model':model,'provider':cfg['provider']}

    def generate_batch(self,items,job,run_id=None,target="summary",mode="all"):
        outputs(target)
        # Fill only missing outputs. Do not spend tokens regenerating an existing
        # summary just because its separate tags have not been generated yet.
        if target=='both':
            groups={'summary':[],'tags':[],'both':[]}
            for p in items:
                fields=needed(p,target,mode,run_id)
                if fields:groups['both' if len(fields)==2 else fields[0]].append(p)
            updated=sum(self.generate_batch(group,job,run_id,kind,mode) for kind,group in groups.items() if group and kind!='both')
            items=groups['both']
            if not items:return updated
        else:updated=0
        batch=[]
        for p in items:
            job.check()
            item={k:p[k] for k in ('key','name','author','description','counts','samples')}
            item.update(categories=p['categories'],existing_summary=p['summary'],existing_tags=p.get('tags',[]))
            item['_archive_path']=self.c.path(p['files'][0])
            try: item.update(content_evidence(self.c.path(p['files'][0])))
            except (OSError,ValueError,zipfile.BadZipFile): pass
            batch.append(item)
        originals={p['key']:p for p in items}
        for result in (self.classification_chunks(batch,job) if target=="summary" else self.classification_chunks(batch,job,target)):
            for key,label in result.items():
                job.check()
                if any(fingerprint(self.c.path(row))!=[row['size'],row['mtime']] for row in originals[key]['files']):
                    raise ValueError('分析期间资源文件发生变化，请更新索引后继续：'+key)
                requested=needed(originals[key],target,mode,run_id)
                label={k:v for k,v in label.items() if k in requested or (k=='category' and 'summary' in requested)}
                if not label:continue
                self.c.label(key,**label,source='ai',summary_run_id=run_id)
                updated+=1
        return updated

    def generate(self,keys,job,overwrite=False,refresh_ai=False):
        if refresh_ai or (overwrite and len(keys)!=1):
            raise ValueError('已有简介仅可在单个包详情中点击「AI 重新分析」覆盖。')
        if len(keys)>200: raise ValueError('每批最多 200 个包，建议先选 5–20 个试用。')
        pkgs=self.c.packages()
        todo=[pkgs[k] for k in dict.fromkeys(keys) if k in pkgs and not pkgs[k]['error']
              and (overwrite or not pkgs[k]['summary_generated'])]
        updated=0
        for i in range(0,len(todo),10):
            job.check()
            job.update(i,len(todo),'正在生成中文简介与分类')
            updated+=self.generate_batch(todo[i:i+10],job)
        job.update(len(todo),len(todo),'中文简介已保存')
        return {'updated':updated,'skipped':len(keys)-len(todo)}
