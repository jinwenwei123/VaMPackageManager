"""Capability evidence and endpoint-scoped user corrections; no inference calls."""
import hashlib
import urllib.parse

FIELDS={'vision':'识图','reasoning':'推理','tool_calling':'工具调用','json_output':'JSON 输出'}
SILICON_DOCS='https://docs.siliconflow.cn/docs/'
# Verified 2026-10-20. Exact IDs only, including Pro prefixes. Entries describe
# capabilities, never model availability, permissions or reasoning effort enums.
SILICON_FACTS={
    'Qwen/Qwen2.5-VL-72B-Instruct':{'vision':('userguide/capabilities/multimodal-vision',True)},
    'moonshotai/Kimi-K2.7-Code':{'vision':('api/chat-completions-post',True)},
    'deepseek-ai/DeepSeek-V4-Flash':{
        'reasoning':('api/chat-completions-post',True),'tool_calling':('api/chat-completions-post',True)},
    'Pro/deepseek-ai/DeepSeek-V4':{'reasoning':('api/chat-completions-post',True)},
    'Pro/zai-org/GLM-5.2':{'reasoning':('api/chat-completions-post',True)},
    'Pro/deepseek-ai/DeepSeek-V3.2':{'reasoning':('userguide/capabilities/reasoning',True)},
    'Pro/zai-org/GLM-5.1':{'tool_calling':('userguide/guides/function-calling',True)},
}


def endpoint(base):
    url=urllib.parse.urlsplit(base.strip().rstrip('/'))
    authority=url.netloc.lower()
    if (url.scheme.lower(),url.port) in (('https',443),('http',80)):
        authority=authority.rsplit(':',1)[0]
    return urllib.parse.urlunsplit((url.scheme.lower(),authority,
                                   url.path.removesuffix('/chat/completions'),'',''))


def scope(base):
    return hashlib.sha256(endpoint(base).encode()).hexdigest()


def overrides(cfg):
    return cfg.get('model_capability_overrides',{}).get(scope(cfg['base_url']),{})


def validate_overrides(value):
    if not isinstance(value,dict) or len(value)>200:
        raise ValueError('模型能力设置须为对象，最多保存200个模型。')
    clean={}
    for model,fields in value.items():
        if not isinstance(model,str) or not model.strip() or model!=model.strip() or len(model)>2000:
            raise ValueError('模型能力设置中的模型名无效。')
        if not isinstance(fields,dict) or fields.keys()-FIELDS.keys() or any(type(v) is not bool for v in fields.values()):
            raise ValueError('模型能力设置只接受识图、推理、工具调用和JSON输出的布尔值；自动项应省略。')
        if fields: clean[model]=dict(fields)
    return clean


def with_tags(info):
    result=dict(info)
    tags=['识图' if info.get('vision') is True else '仅文字' if info.get('vision') is False else '识图未确认']
    for key in ('reasoning','tool_calling','json_output'):
        if info.get(key) is True: tags.append(FIELDS[key])
        elif info.get(key) is False: tags.append('不支持'+FIELDS[key])
    if info.get('hidden'): tags.append('CLI 隐藏 / 专用')
    context=info.get('context_window')
    if type(context) is int and context>0: tags.append(f'{context//1024}K 上下文')
    result['tags']=tags
    return result


def resolve(info,base,manual=None):
    result=dict(info)
    sources=dict(info.get('capability_sources',{}))
    if endpoint(base)=='https://api.siliconflow.cn/v1':
        for key,(page,value) in SILICON_FACTS.get(info['id'],{}).items():
            if result.get(key) is None:
                result[key]=value
                sources[key]={'kind':'official','url':SILICON_DOCS+page,'checked':'2026-10-20'}
    for key,value in (manual or {}).items():
        if key in FIELDS and type(value) is bool:
            result[key]=value;sources[key]={'kind':'manual'}
    result['capability_sources']=sources
    return with_tags(result)
