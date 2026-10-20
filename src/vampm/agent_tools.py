"""Stable, explicit package tools shared by chat providers and the local HTTP API."""
import base64
import threading
import zipfile

from .metadata import content_evidence
from .covers import read_cover
from .activation import ActivationChoices
from .util import CATEGORIES, Cancelled


def spec(name, description, properties=None, required=()):
    return {'name':name, 'description':description, 'inputSchema':{
        'type':'object','properties':properties or {},'required':list(required),'additionalProperties':False}}


TEXT={'type':'string'}
KEYS={'type':'array','items':TEXT,'maxItems':20000,'description':'来自搜索结果的精确 key，包含版本。在对话中使用 ["@selected"] 可引用本轮界面全部已选包，不能和其他 key 混用。'}
TOOLS = [
    spec('search_packages','搜索真实本地包；支持中文分类、作者/包名/简介/标签关键词（多个词以空格分隔，全部匹配）。用于推荐前查证，勿编造资源。',{
        'q':TEXT,'category':{'type':'string','enum':['']+CATEGORIES},
        'state':{'type':'string','enum':['','active','disabled','favorite','pinned','errors','warnings','unlabeled']},
        'sort':{'type':'string','enum':['name','new','size']},'page':{'type':'integer','minimum':1},
        'limit':{'type':'integer','minimum':1,'maximum':50}}),
    spec('inspect_package','读取包的全部分类、内容标签、简介、作者说明、场景线索、内容清单和依赖；涉及外观再调用 view_package_cover 看图核实。',{'key':TEXT},['key']),
    spec('view_package_cover','查看一个真实包的封面图片，仅对话模型支持识图时发送；每轮最多4张，仅核实可见外观，不证明图中所有内容随包提供。',{'key':TEXT},['key']),
    spec('show_packages','选定推荐后调用，以卡片展示这些真实本地包，卡片在回复后出现，可打开详情和收藏。只列本轮实际推荐，最多 12 个。',
         {'keys':dict(KEYS,maxItems=12),'title':TEXT},['keys']),
    spec('list_folders','查找已索引的资源文件夹及包数，包含停用库；返回当前收藏目录。创建文件夹启动方案前可用此工具核对路径。',
         {'q':TEXT,'limit':{'type':'integer','minimum':1,'maximum':100}}),
    spec('set_favorites','批量收藏/取消收藏。在 收藏/主分类 与普通主分类目录之间移动；按当前已应用方案同步启停；未应用方案时仅保留固定基础包，收藏进入停用库；基础收藏已应用时自动启用新收藏及依赖。支持恢复。',
         {'keys':KEYS,'favorite':{'type':'boolean'}},['keys','favorite']),
    spec('check_dependencies','核对主包及其递归依赖；省略 keys 则检查整个库。',{'keys':KEYS}),
    spec('manage_profiles','列出、创建/更新或删除启动方案；保存不启用。来源三选一：keys、folder、favorites=true。文件夹/收藏方案动态纳入后来新增的包，预览和启用时自动包含递归依赖。列表只预览主包，请用 profile_id 启用完整方案。',{
        'action':{'type':'string','enum':['list','save','delete']},'name':TEXT,'keys':KEYS,'id':TEXT,
        'folder':TEXT,'favorites':{'type':'boolean'}},['action']),
    spec('plan_files','生成文件操作预览，不移动文件。organize 按主分类归档并按当前方案校正全库启停，未应用方案时仅启用固定基础包；move 转移到库内指定文件夹；activate 启用主包及所选副本的可用依赖，缺失依赖仅提示、不阻止执行，返回同名副本选项；stage_all 将非固定包移入停用库并按主分类整理，收藏单独分类；pin 固定基础包及依赖，常驻基础包目录；unpin 解除手动固定；restore_all 全部启用；dedup 清理经过内容校验的重复副本。remember_copies=true 会保存副本偏好。',{
        'kind':{'type':'string','enum':['organize','move','activate','restore_all','dedup','stage_all','pin','unpin']},'keys':KEYS,
        'folder':{'type':'string','description':'仅 move 使用，例如 收藏/人物/精选。必须为库内相对路径。'},
        'profile_id':{'type':'string','description':'activate 时可直接指定完整启动方案，自动解析当前包和依赖，不能同时传 keys。'},
        'duplicate_mode':{'type':'string','enum':['select','all'],'description':'仅 activate：select 指定副本；all 保留所有副本在不同目录，VaM 仍只采用其中一份，不合并。省略则沿用已记住的选择。'},
        'copy_choices':{'type':'object','additionalProperties':{'type':'string','pattern':'^[0-9a-fA-F]{64}$'},'maxProperties':20000,
                        'description':'仅 activate：包 key 到完整 SHA-256 的对应表，使用预览 copy_conflicts 中的实际指纹；替换此方案的选择表。'},
        'remember_copies':{'type':'boolean','description':'仅 activate：保存本次模式和副本选择，按方案独立记忆；未提供 profile_id 时记为临时选择偏好。默认 false。'}},['kind']),
    spec('apply_plan','执行 plan_files 返回的精确计划 id。只在用户要求执行此操作时调用；推荐/讨论不授权移动。会再次验证文件状态并留下恢复记录。',{'id':TEXT},['id']),
    spec('library_task','运行索引、内容校验、依赖补包、恢复或启动；启动前如需按需加载应先创建并执行 activate 计划。history 读取可恢复记录。download_dependencies 下载已找到的 Hub、作者 GitHub 或用户添加的可信站点匹配资源。',{
        'action':{'type':'string','description':'scan/deep_scan 会移动文件以同步当前启用方案；没有已应用方案时普通包停用。','enum':['scan','deep_scan','audit','find_dependencies','download_dependencies','history','undo','launch']},
        'keys':KEYS,'id':TEXT,'names':{'type':'array','items':TEXT}},['action']),
    spec('get_status','读取资源库计数、后台任务状态和未完成文件操作。'),
    spec('label_package','修改包的简介、分类或收藏状态。',{'key':TEXT,'category':{'type':'string','enum':CATEGORIES},
        'summary':TEXT,'favorite':{'type':'boolean'}},['key']),
]


def validate(name, args):
    schema=next((t['inputSchema'] for t in TOOLS if t['name']==name),None)
    if schema is None: raise ValueError('未知的包管理工具')
    if not isinstance(args,dict): raise ValueError('工具参数必须为对象')
    if set(args)-set(schema['properties']): raise ValueError('包含不支持的工具参数')
    if set(schema['required'])-set(args): raise ValueError('缺少必填工具参数')
    for k,v in args.items():
        s=schema['properties'][k];typ=s['type']
        valid=(isinstance(v,str) if typ=='string' else type(v) is bool if typ=='boolean'
               else type(v) is int if typ=='integer' else isinstance(v,dict) if typ=='object' else isinstance(v,list))
        if not valid: raise ValueError('参数类型无效：'+k)
        if typ=='string' and len(v)>2000 and not (name=='label_package' and k=='summary'):
            raise ValueError('参数文本过长：'+k)
        if 'enum' in s and v not in s['enum']: raise ValueError('参数选项无效：'+k)
        if typ=='integer' and not s.get('minimum',v)<=v<=s.get('maximum',v): raise ValueError('参数范围无效：'+k)
        if typ=='array' and (len(v)>s.get('maxItems',20000) or any(not isinstance(x,str) or len(x)>500 for x in v)):
            raise ValueError('包列表格式无效')
        if k=='copy_choices': ActivationChoices.validate('select',v)


def invoke(app, name, args, cancel=None, progress=None):
    validate(name,args)
    args=dict(args)
    if 'keys' in args: args['keys']=[k.casefold() for k in args['keys']]
    if 'key' in args: args['key']=args['key'].casefold()
    if cancel and cancel.is_set(): raise Cancelled('对话任务已停止')

    def post(route, data):
        out=app.post('/api/'+route,data,from_agent=True)
        if isinstance(out,dict) and out.get('id') in app.jobs.jobs:
            job=app.jobs.jobs[out['id']]
            while job.status=='running':
                if cancel and cancel.wait(.1):
                    job.cancel.set()
                    # An already-set Event returns immediately; avoid spinning while
                    # a download waits for the current network read to finish.
                    import time
                    time.sleep(.1)
                elif not cancel:
                    import time
                    time.sleep(.1)
                if progress: progress(job.public(result=False))
            if job.status!='done': raise ValueError(job.error or '任务未完成')
            return job.result
        return out

    if name=='search_packages':
        return app.c.list(dict(args,limit=args.get('limit',20)))
    if name=='inspect_package':
        out=app.c.detail(args['key'])
        p=app.c.packages()[args['key']]
        try:out['content_evidence']=content_evidence(app.c.path(p['files'][0]))
        except (OSError,ValueError,zipfile.BadZipFile):pass
        out['dependencies']=out['dependencies'][:150]
        out['files']=out['files'][:30]
        out['deps']=out['deps'][:150]
        return out
    if name=='view_package_cover':
        p=app.c.packages().get(args['key'])
        if not p:raise ValueError('包不存在，请重新搜索')
        cfg=app.ai.settings(True)
        model=cfg['codex_model'] if cfg['provider']=='codex' else cfg['api_model']
        if not app.ai.models.supports_images(cfg,cancel or threading.Event(),model=model):
            return {'key':p['key'],'available':False,'reason':'当前对话模型不支持识图或能力未确认，请根据文字与标签判断，不要声称看过图片。'}
        preview=read_cover(app.c.path(p['files'][0]))
        if not preview:return {'key':p['key'],'available':False,'reason':'包内没有可读取的封面'}
        data,mime,entry=preview
        return {'key':p['key'],'available':True,'source':entry,'categories':p['categories'],'tags':p.get('tags',[]),
                'note':'仅证明可见外观；展示模特、衣服、背景不代表本包包含。',
                '_images':[{'key':p['key'],'url':'data:'+mime+';base64,'+base64.b64encode(data).decode('ascii')}]}
    if name=='show_packages':
        keys=list(dict.fromkeys(args['keys']))
        if not keys or any(k not in app.c.packages() for k in keys):raise ValueError('推荐卡片只能使用本地存在的包 key')
        return {'title':str(args.get('title') or '为你推荐')[:80],
                'items':[app.c.public(app.c.packages()[k]) for k in keys]}
    if name=='list_folders':return app.get('/api/folders',dict(args,limit=args.get('limit',50)))
    if name=='set_favorites':return post('favorite',args)
    if name=='check_dependencies':
        keys=args.get('keys');missing=app.c.missing(keys)
        closure=app.c.closure(keys) if keys else None
        return {'missing_count':len(missing),'missing':missing[:100],
                'included_count':len(closure['keys']) if closure else None,
                'invalid_or_missing':closure['missing'][:100] if closure else []}
    if name=='manage_profiles':
        action=args.pop('action')
        if action in ('list','save'):
            rows=app.ops.profiles() if action=='list' else post('profile',args)
            return [dict(p,keys=p['keys'][:40],keys_total=len(p['keys']),keys_truncated=len(p['keys'])>40) for p in rows]
        return post('delete-profile',{'id':args.get('id','')})
    if name=='plan_files': return post('plan',args)
    if name=='apply_plan': return post('apply',args)
    if name=='get_status': return app.get('/api/status',{})
    if name=='label_package': return post('label',args)
    if name=='library_task':
        action=args.pop('action')
        if action=='history': return app.ops.history()[:40]
        if action in ('scan','deep_scan'): return post('scan',{'deep':action=='deep_scan'})
        routes={'audit':'audit','find_dependencies':'find-dependencies','download_dependencies':'install-dependencies',
                'undo':'undo','launch':'launch'}
        return post(routes[action],args)
    raise ValueError('工具尚未实现')
