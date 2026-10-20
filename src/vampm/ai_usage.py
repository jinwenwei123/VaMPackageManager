"""Persist numeric API usage only; never store credentials, prompts or responses."""
import hashlib
import json
import threading
import urllib.parse

from .util import atomic_json, read_json, stamp


class APIUsage:
    def __init__(self, folder):
        self.path=folder/'ai-usage.json'
        self.lock=threading.RLock()
        self.rows=read_json(self.path,{})
        if not isinstance(self.rows,dict):self.rows={}

    def record(self,cfg,model,purpose,response):
        if isinstance(response,(str,bytes)):
            try:response=json.loads(response)
            except (ValueError,TypeError):return None
        usage=response.get('usage') if isinstance(response,dict) else None
        if not isinstance(usage,dict):return None
        counters={k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens',
                                     'prompt_cache_hit_tokens','prompt_cache_miss_tokens')
                  if type(usage.get(k)) is int and usage[k]>=0}
        details=usage.get('prompt_tokens_details')
        if 'prompt_cache_hit_tokens' not in counters and isinstance(details,dict):
            cached=details.get('cached_tokens')
            if type(cached) is int and cached>=0:counters['prompt_cache_hit_tokens']=cached
        prompt=counters.get('prompt_tokens')
        hit=counters.get('prompt_cache_hit_tokens')
        miss=counters.get('prompt_cache_miss_tokens')
        if prompt is None and hit is not None and miss is not None:
            prompt=hit+miss;counters['prompt_tokens']=prompt
        if hit is not None and prompt is not None and hit<=prompt:
            counters.update(prompt_cache_hit_tokens=hit,prompt_cache_miss_tokens=prompt-hit)
            counters['cache_observed_tokens']=prompt
        elif hit is not None or miss is not None:
            counters.pop('prompt_cache_hit_tokens',None);counters.pop('prompt_cache_miss_tokens',None)
        if not counters:return None
        source=hashlib.sha256((cfg['provider']+'|'+cfg['base_url'].rstrip('/')+'|'+model+'|'+purpose).encode()).hexdigest()
        with self.lock:
            row=self.rows.setdefault(source,dict(provider=cfg['provider'],model=model,purpose=purpose,requests=0,
                endpoint=urllib.parse.urlsplit(cfg['base_url']).hostname))
            row['requests']+=1;row['updated']=stamp();row['last']=counters
            for k,v in counters.items():row[k]=row.get(k,0)+v
            # Telemetry failure must never turn a successful paid request into a retry.
            try:atomic_json(self.path,self.rows)
            except OSError:pass
        return dict(counters)

    def public(self):
        with self.lock:return json.loads(json.dumps(list(self.rows.values())))
