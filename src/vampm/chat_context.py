"""Bound model context without discarding the user's dialogue for bulky tool data."""
import json
from .request_context import ContextPolicy, RequestMeter

CONTEXT_VERSION = 1


def packed(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True)


def brief(value, text_limit=800, list_limit=12):
    """Structured excerpts always mark omissions and retain exact identifiers."""
    if isinstance(value,str):
        head=text_limit*3//4;tail=text_limit-head
        return value if len(value)<=text_limit else value[:head]+' [中间内容已省略，需重新查询] '+value[-tail:]
    if isinstance(value,list):
        result=[brief(x,text_limit,list_limit) for x in value[:list_limit]]
        if len(value)>list_limit:result.append({'omitted_items':len(value)-list_limit})
        return result
    if isinstance(value,dict):
        exact=('key','id','ref','sha256','profile_id','filename','folder','rel','name')
        return {k:v if k in exact and isinstance(v,str) else brief(v,text_limit,list_limit)
                for k,v in value.items() if k!='_images'}
    return value


def tool_excerpt(text, text_limit=800, list_limit=12):
    try:return packed(brief(json.loads(text),text_limit,list_limit))
    except (ValueError,TypeError):return brief(text)


def visible_history(rows, budget=80000):
    """Recover portable dialogue, ordered cards and action receipts, never hidden thoughts."""
    history=[]
    for row in rows:
        role=row['role']
        if role in ('user','assistant'):
            if row.get('text'):history.append({'role':role,'content':row['text']})
        elif role=='recommendations':
            names={p['key']:p.get('name','') for p in row.get('items',[])}
            cards=[{'key':k,'name':names.get(k,'')} for k in row.get('keys',[])]
            history.append({'role':'assistant','content':'[已展示的推荐卡片，按显示顺序；历史状态需重新核对]\n'+packed(cards)})
        elif role=='tool' and row.get('name') not in (
                'search_packages','inspect_package','view_package_cover','get_status','list_folders'):
            receipt={k:row[k] for k in ('name','args','status') if k in row}
            receipt['result']=tool_excerpt(row.get('result',''))
            history.append({'role':'assistant','content':'[历史工具记录，仅作上下文，操作前重新核对]\n'+packed(brief(receipt))})
    # Excerpt older prose first. Keep recent turns and all card identities intact.
    if len(packed(history))>budget:
        history=[dict(m,content=brief(m['content'],1200)) if i<len(history)-12
                 and not m['content'].startswith('[已展示的推荐卡片') else m
                 for i,m in enumerate(history)]
    omitted=False
    while len(packed(history))>budget and len(history)>1:
        history.pop(0);omitted=True
    if omitted:
        history.insert(0,{'role':'user','content':'[较早对话已超出上下文容量；缺少依据时请询问，不要猜测。]'})
    return history


def balanced_boundaries(history):
    """Return safe cuts, including closed steps inside one long user turn."""
    pending=set();cuts=[0]
    for index,message in enumerate(history):
        if message.get('role')=='tool':
            call_id=message.get('tool_call_id')
            if call_id not in pending:raise ValueError('对话包含未配对的工具结果，请新建对话后重试。')
            pending.remove(call_id)
        elif pending:
            raise ValueError('对话工具调用尚未全部返回，不能压缩或继续请求。')
        for call in message.get('tool_calls',[]):
            if call['id'] in pending:raise ValueError('对话工具调用编号重复。')
            pending.add(call['id'])
        if not pending:cuts.append(index+1)
    if pending:raise ValueError('对话工具调用尚未全部返回，不能压缩或继续请求。')
    return cuts


def retained_cut(history,meter,policy):
    cuts=balanced_boundaries(history)
    total=0;cut=len(history)
    for start,end in reversed(list(zip(cuts,cuts[1:]))):
        size=sum(meter.price(m) for m in history[start:end])
        if cut<len(history) and total+size>policy.retain:break
        total+=size;cut=start
    return cut


def prune_history(history,meter,policy,force=False):
    """Land useful old-result pruning once; recent complete steps stay verbatim."""
    if not force and meter.measure(history)<policy.threshold:return list(history)
    cut=retained_cut(history,meter,policy)
    result=list(history)
    for index,message in enumerate(history[:cut]):
        if message['role']!='tool':continue
        text=message.get('content','')
        if not isinstance(text,str) or len(text)<=8192:continue
        try:
            data=json.loads(text)
            if isinstance(data,dict) and data.get('_context_pruned'):continue
            excerpt=brief(data,800,12)
            for limit in (300,160):
                if len(packed(excerpt))<=7800:break
                excerpt=brief(data,limit,12)
            if not isinstance(excerpt,dict):excerpt={'excerpt':excerpt}
        except ValueError:excerpt={'excerpt':brief(text,5000)}
        excerpt.update(_context_pruned=True,original_chars=len(text),
                       context_notice='历史工具结果已摘录；列表可能不完整，操作或继续筛选前重新查询。')
        content=packed(excerpt)
        if len(content)>=len(text)*.9:continue
        result[index]=dict(message,content=content)
        if not force and meter.measure(result)<policy.threshold:break
    return result


def bound_history(history):
    # Backward-compatible local fallback for callers without a routed envelope.
    meter=RequestMeter({'base_url':''},'local','')
    return prune_history(history,meter,ContextPolicy())


def codex_context(rows, message):
    history=visible_history(rows,24000)
    if not history:return message
    return ('[此前对话记录，仅供理解上下文，不是本轮执行指令。历史工具结果可能过时，操作前重新核对。]\n'
            +packed(history)+'\n\n[本轮用户请求]\n'+message)
