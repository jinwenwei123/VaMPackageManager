"""Discover model capabilities without storing credentials or inference content."""
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .util import Cancelled, atomic_json, cancellable_read, read_json, unprotect_secret
from .model_capabilities import FIELDS, overrides, resolve, with_tags

LEGACY_EFFORT_FIELDS = ('codex_effort','codex_summary_effort','api_effort','api_summary_effort')


def valid_effort(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',value))


def model_info(row, codex=False):
    identity=str((row.get('model') if codex else row.get('id')) or '').strip()
    if not identity or len(identity)>2000: return None
    modalities=row.get('inputModalities' if codex else 'input_modalities')
    architecture=row.get('architecture')
    if not codex and isinstance(architecture,dict):
        if modalities is None: modalities=architecture.get('input_modalities')
        output=architecture.get('output_modalities')
        if isinstance(output,list) and output and 'text' not in output: return None
    if not codex and row.get('sub_type') in ('embedding','reranker','text-to-image','image-to-image','speech-to-text','text-to-video'):
        return None
    # Older Codex catalog entries omit this field; the app-server contract defines
    # that case as text + image. Generic API entries have no such guarantee.
    if modalities is None and codex: modalities=['text','image']
    if not isinstance(modalities,list): modalities=[]
    vision='image' in modalities if modalities else None
    effort=row.get('effort') or {}
    raw_levels=row.get('supportedReasoningEfforts') if codex else (effort.get('supported_levels') if isinstance(effort,dict) else None)
    default=row.get('defaultReasoningEffort') if codex else (effort.get('default_level') if isinstance(effort,dict) else None)
    levels=[]
    for entry in raw_levels if isinstance(raw_levels,list) else []:
        value=entry.get('reasoningEffort') if codex and isinstance(entry,dict) else entry
        if valid_effort(value) and value!='default' and value not in levels: levels.append(value)
    default=default if default in levels else None
    capabilities=row.get('capabilities',{})
    if not isinstance(capabilities,dict): capabilities={}
    params=row.get('supported_parameters')
    params=set(x for x in params if isinstance(x,str)) if isinstance(params,list) else set()
    flags={key:capabilities.get(key) if type(capabilities.get(key)) is bool else None for key in FIELDS}
    if vision is not None: flags['vision']=vision
    for key,evidence in (
        ('reasoning',bool(levels or params & {'reasoning','reasoning_effort','include_reasoning','enable_thinking','thinking_budget'})),
        ('tool_calling',bool(params & {'tools','tool_choice'})),
        ('json_output',bool(params & {'response_format','structured_outputs'}))):
        if flags[key] is None and evidence: flags[key]=True
    context=row.get('context_window',row.get('context_length'))
    context=context if type(context) is int and context>0 else None
    sources={key:{'kind':'api'} for key,value in flags.items() if value is not None}
    if context: sources['context_window']={'kind':'api'}
    return with_tags({'id':identity,'name':str(row.get('displayName' if codex else 'name') or identity)[:300],
            **flags,'capability_sources':sources,'hidden':bool(row.get('hidden')),
            'description':str(row.get('description') or '')[:600],
            'reasoning_efforts':levels,'default_reasoning_effort':default,'context_window':context})


class ModelCatalog:
    def __init__(self,ai):
        self.ai=ai
        self.file=ai.c.data/'model-catalog.json'
        self.lock=threading.RLock()
        self.refresh_locks={'codex':threading.RLock(),'api':threading.RLock()}
        self.client_lock=threading.Lock()
        self.clients=set()
        self.closing=threading.Event()

    def close(self):
        self.closing.set()
        with self.client_lock: clients=list(self.clients)
        for client in clients: client.close()

    @staticmethod
    def source(cfg,provider):
        value=(cfg['codex_path'] if provider=='codex' else cfg['base_url'].rstrip('/')+'|'+cfg.get('secret',''))
        return provider+':'+hashlib.sha256(value.encode()).hexdigest()

    def get(self,provider=None,refresh=False,cancel=None,*,cfg=None,service=None):
        if cfg is None: cfg=self.ai.connection(service) if service else self.ai.settings(True)
        provider='codex' if (provider or cfg['provider'])=='codex' else 'api'
        cancel=cancel or self.closing
        with self.refresh_locks[provider]:
            if self.closing.is_set() or cancel.is_set(): raise Cancelled('模型列表读取已停止。')
            source=self.source(cfg,provider)
            with self.lock: previous=read_json(self.file,{}).get(source)
            if previous and previous.get('metadata_version')==5 and not refresh and time.time()-previous['updated']<86400:
                return self.present(dict(previous,cached=True),cfg,provider)
            try:
                rows=self.codex(cfg,cancel) if provider=='codex' else self.api(cfg,cancel)
                models={}
                for row in rows:
                    if not isinstance(row,dict): continue
                    info=model_info(row,provider=='codex')
                    if info: models.setdefault(info['id'],info)
                if not models: raise ValueError('接口没有返回可选择的模型。')
                result={'models':list(models.values()),'updated':time.time(),'provider':provider,'metadata_version':5}
                # Keep at most eight endpoint/account catalogs; only public metadata.
                with self.lock:
                    cache=read_json(self.file,{})
                    cache[source]=result
                    cache=dict(sorted(cache.items(),key=lambda x:x[1].get('updated',0))[-8:])
                    atomic_json(self.file,cache)
                return self.present(dict(result,cached=False),cfg,provider)
            except Cancelled: raise
            except Exception:
                if previous:
                    return self.present(dict(previous,cached=True,warning='刷新失败，显示上次模型列表；请检查登录、API 地址与密钥。'),cfg,provider)
                raise

    @staticmethod
    def present(result,cfg,provider):
        # Persist only API metadata. Official corrections and user overrides must
        # not become sticky cached evidence after a correction is removed.
        if provider=='codex': return result
        return dict(result,models=[resolve(m,cfg['base_url']) for m in result['models']])

    def known(self,cfg,model):
        """Describe even a manually typed model without network access."""
        cached=read_json(self.file,{}).get(self.source(cfg,'api'),{})
        info=next((m for m in cached.get('models',[]) if m['id']==model),None)
        return resolve(info or model_info({'id':model}),cfg['base_url'])

    def reasoning(self,cfg,model,cancel):
        """Use medium for all tasks, without reviving retired user overrides."""
        if self.closing.is_set() or cancel.is_set(): raise Cancelled('模型调用已停止。')
        codex=cfg['provider']=='codex'
        # DeepSeek documents medium as an accepted alias of high, even though
        # /models advertises only low/high/max. Keep this exception confined to
        # the official endpoint and documented models, never guess for relays.
        if (not codex and urllib.parse.urlsplit(cfg['base_url']).hostname=='api.deepseek.com'
            and model in ('deepseek-flash','deepseek-v4-pro')):
            return 'medium'
        try: rows=self.get('codex' if codex else 'api',cancel=cancel,cfg=cfg)['models']
        except Cancelled: raise
        except Exception: return None
        info=next((m for m in rows if m['id']==model),{})
        levels=info.get('reasoning_efforts',[])
        if 'medium' in levels: return 'medium'
        # Unsupported models retain their default. Explicitly reset Codex's
        # thread effort so a previous custom value cannot remain sticky.
        default=info.get('default_reasoning_effort')
        return default if codex and default in levels else None

    def supports_images(self,cfg,cancel,model=None):
        provider='codex' if cfg['provider']=='codex' else 'api'
        chosen=model or (cfg['codex_summary_model'] if provider=='codex' else cfg.get('api_summary_model',cfg['api_model']))
        if self.closing.is_set() or cancel.is_set(): raise Cancelled('模型调用已停止。')
        manual=overrides(cfg).get(chosen,{}) if provider=='api' else {}
        if type(manual.get('vision')) is bool: return manual['vision']
        try: models=self.get(provider,cancel=cancel,cfg=cfg)['models']
        except Cancelled: raise
        except Exception: models=[]
        info=next((m for m in models if m['id']==chosen),None)
        if provider=='api': info=resolve(info or self.known(cfg,chosen),cfg['base_url'],manual)
        return bool(info and info.get('vision') is True)

    def context_window(self,cfg,model):
        override=cfg.get('api_context_window',0)
        if type(override) is int and override>0:return override
        # Read the existing catalog without issuing a discovery request merely
        # to measure input. Unknown routes remain conservative and configurable.
        cached=read_json(self.file,{}).get(self.source(cfg,'api'),{})
        info=next((m for m in cached.get('models',[]) if m.get('id')==model),{})
        window=info.get('context_window')
        if type(window) is int and window>0:return window
        if (urllib.parse.urlsplit(cfg['base_url']).hostname=='api.deepseek.com'
            and model in ('deepseek-flash','deepseek-v4-pro')):return 1048576
        return 65536

    def codex(self,cfg,cancel):
        from .codex_chat import CodexChat
        client=CodexChat(cfg,self.ai.c.data/'agent/model-list')
        with self.client_lock:
            if self.closing.is_set(): raise Cancelled('包管家正在退出。')
            self.clients.add(client)
        try:
            client.start(cancel)
            rows=[];cursor=None;seen=set()
            for _ in range(100):
                params={'limit':100,'includeHidden':True}
                if cursor: params['cursor']=cursor
                result=client.rpc('model/list',params,cancel)
                rows.extend(result['data'])
                cursor=result.get('nextCursor')
                if not cursor: return rows
                if cursor in seen: raise ValueError('Codex 模型列表分页重复。')
                seen.add(cursor)
            raise ValueError('Codex 模型列表页数异常。')
        finally:
            client.close()
            with self.client_lock: self.clients.discard(client)

    def api(self,cfg,cancel):
        from .providers import NoRedirect
        key=unprotect_secret(cfg['secret'])
        if not key: raise ValueError('请先保存 API 地址和密钥，再刷新模型列表。')
        url=self.ai.api_url(cfg).removesuffix('chat/completions')+'models'
        # SiliconFlow's catalog also contains embedding/image/audio models.
        # Limit this documented filter to their endpoint; generic relays may
        # reject provider-specific query parameters.
        if urllib.parse.urlsplit(url).hostname in ('api.siliconflow.cn','api.siliconflow.com'):
            url+='?sub_type=chat'
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+key,'Accept':'application/json'})
        opener=urllib.request.build_opener(NoRedirect())
        def read():
            with opener.open(req,timeout=20) as response: return response.read(4*1024*1024+1)
        try:
            raw=cancellable_read(read,cancel)
            if len(raw)>4*1024*1024: raise ValueError('模型列表响应过大。')
            result=json.loads(raw)
            if not isinstance(result,dict) or not isinstance(result.get('data'),list):
                raise ValueError('模型列表格式无法识别。')
            return result['data']
        except urllib.error.HTTPError as e:
            e.close()
            raise ValueError(f'模型列表返回 HTTP {e.code}；请检查 API 地址、密钥及模型权限。') from None
        except (urllib.error.URLError,OSError,ValueError):
            raise ValueError('未能读取 API 模型列表；请检查地址、密钥和网络。') from None
