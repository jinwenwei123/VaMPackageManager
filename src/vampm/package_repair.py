"""Unified, typed review surface for filename and metadata syntax repairs."""
import uuid
from .json_repair import JsonRepairs
from .name_repair import NameRepairs
from .util import atomic_json,read_json


class PackageRepairs:
    def __init__(self,catalog,ops,ai):
        self.c=catalog;self.names=NameRepairs(catalog,ops,ai);self.json=JsonRepairs(catalog,ops,ai)
        self.path=catalog.data/'package-repair-preview.json'

    def status(self):
        names=self.names.status();meta=self.json.status();old=read_json(self.path,{})
        return {'eligible':names['eligible']+meta['eligible'],'names':names['eligible'],'json':meta['eligible'],
                'skipped':meta['skipped'],'analyzed':names['analyzed']+meta['analyzed'],
                'preview_ready':bool(old and old.get('signature')==self.c.signature())}

    def analyze(self,job,refresh=False):
        names=self.names.analyze(job,refresh);meta=self.json.analyze(job,refresh)
        self.json._inventory_cache=None
        p={'id':uuid.uuid4().hex,'signature':self.c.signature(),'names':names,'json':meta}
        atomic_json(self.path,p);return self.public(p)

    def public(self,p):
        meta=p['json'];names=p['names']
        items=[dict(i,type='name',repairable=True) for i in names['items']]
        items.extend({k:v for k,v in i.items() if k not in ('row','original','fixed','bom')} for i in meta['items'])
        # A metadata-warning package can also have a malformed filename. JSON
        # first avoids coupling a rename transaction to an archive rewrite.
        json_ids={i['id'] for i in items if i['type']=='json'}
        for item in items:
            if item['type']=='name' and item['id'] in json_ids:
                item.update(repairable=False,reason='请先修复此包的 JSON，再重新分析文件名。')
        return {'package_repair_preview':True,'id':p['id'],'model':meta['model'],'items':items,'skipped':meta['skipped']}

    def last_preview(self):
        p=read_json(self.path,{})
        if not p or p['signature']!=self.c.signature():raise ValueError('修复建议已过期，请重新分析')
        return self.public(p)

    def apply(self,token,selections,job):
        p=read_json(self.path,{})
        if not p or p['id']!=token or p['signature']!=self.c.signature():raise ValueError('修复建议已过期，请重新分析')
        if not isinstance(selections,list) or not selections:raise ValueError('请选择需要修复的项目')
        allowed={(i['type'],i['id']) for i in self.public(p)['items'] if i['repairable']};seen=set()
        for s in selections:
            key=(s.get('type'),s.get('id'))
            if key not in allowed or key in seen:raise ValueError('修复项目无效或重复')
            seen.add(key)
        names=[s for s in selections if s['type']=='name'];ids=[s['id'] for s in selections if s['type']=='json']
        results=[]
        # Distinct rows, independent durable transactions. If a later group
        # fails, completed groups remain in operation history for undo.
        if names:results.append(self.names.apply(p['names']['id'],names,job))
        if ids:results.append(self.json.apply(p['json']['id'],ids,job))
        return {'kind':'repair_packages','repaired':sum(r['repaired'] for r in results),'operations':results,
                'message':'所选修复已完成。原启停位置保留，各类修复分别记录，可按倒序恢复。'}
