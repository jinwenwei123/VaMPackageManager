"""Incremental, route-scoped request pricing and reusable generation envelopes.

Policy follows DeepSeek Harness's usage-anchor and pressure/retained-tail design;
this local estimator is CJK-aware and does not treat image Base64 as text tokens.
"""
import hashlib
import json
import math
from collections import OrderedDict
from functools import lru_cache


def canonical(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True)


def text_tokens(text):
    ascii_bytes=len(text.encode('ascii','ignore'))
    return math.ceil(ascii_bytes/4+(len(text.encode('utf-8'))-ascii_bytes)/3)


def message_tokens(message):
    message=dict(message)
    content=message.get('content')
    images=0
    if isinstance(content,list):
        parts=[]
        for part in content:
            if part.get('type')=='image_url':
                images+=1
                parts.append({'type':'image_url'})
            else:parts.append(part)
        message['content']=parts
    # Image token pricing is route-owned. Reserve conservatively until usage
    # anchors the actual request; never price hundreds of KB of Base64 as prose.
    return text_tokens(canonical(message))+8+images*2048


class RequestMeter:
    def __init__(self,cfg,model,system,tools=(),anchor=None):
        self.signature=hashlib.sha256(canonical([cfg.get('provider'),cfg['base_url'].rstrip('/'),model,
                                               system,tools,cfg.get('_chat_effort')]).encode()).hexdigest()
        self.header_tokens=message_tokens({'role':'system','content':system})+(text_tokens(canonical(tools))+8 if tools else 0)
        self.cache=OrderedDict()
        self.priced_messages=0
        self.anchor=anchor if isinstance(anchor,dict) and anchor.get('signature')==self.signature else {}

    def price(self,message):
        key=id(message)
        cached=self.cache.get(key)
        if cached is not None and cached[0] is message:return cached[1]
        value=message_tokens(message);self.priced_messages+=1
        self.cache[key]=(message,value)
        if len(self.cache)>2048:self.cache.popitem(last=False)
        return value

    def raw(self,history):return self.header_tokens+sum(self.price(m) for m in history)

    def measure(self,history):
        raw=self.raw(history)
        actual=self.anchor.get('actual');previous=self.anchor.get('estimated')
        if type(actual) is int and type(previous) is int:
            return max(raw,actual+raw-previous)
        return raw

    def observe(self,history,usage):
        actual=usage.get('prompt_tokens') if isinstance(usage,dict) else None
        if type(actual) is int and actual>=0:
            self.anchor={'signature':self.signature,'actual':actual,'estimated':self.raw(history)}
        return dict(self.anchor)


class ContextPolicy:
    def __init__(self,window=65536,output=4096):
        self.window=window;self.output=output
        self.headroom=min(4096,max(512,window//32))
        self.threshold=min(int(window*.8),window-output-self.headroom)
        self.retain=min(16000,max(512,int(max(0,window-output)*.16)))
        self.summary_output=min(4096,max(512,window//16))
        if self.threshold<=0:raise ValueError('模型上下文容量不足以容纳输出预留，请检查上下文容量设置。')


@lru_cache(maxsize=32)
def generation_system(instruction,schema_text):
    return instruction+'\n输出 JSON schema：\n'+schema_text


def context_overflow(error):
    """Recognize provider-confirmed overflow, never auth/quota/ordinary 400s."""
    if error.code not in (400,413,422):return False
    try:
        raw=error.read(65536)
        body=json.loads(raw)
        detail=body.get('error',{}) if isinstance(body,dict) else {}
        if isinstance(detail,str):detail={'message':detail}
        if not isinstance(detail,dict):return False
        code=str(detail.get('code') or '').casefold()
        message=str(detail.get('message') or '').casefold()
        return (code in ('context_length_exceeded','context_window_exceeded','max_context_length')
                or any(s in message for s in ('maximum context length','context window exceeded',
                                              'exceeds the context','context length exceeded')))
    except (ValueError,TypeError,OSError):return False
