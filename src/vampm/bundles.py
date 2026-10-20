"""Keep a downloaded resource folder and its sidecars together, without copying VARs."""
import copy
from collections import defaultdict
from pathlib import PurePosixPath

from .util import CATEGORIES, atomic_json, read_json, safe_path


class Bundles:
    def __init__(self,catalog):
        self.c=catalog;self.path=catalog.data/'resource-folders.json'

    def state(self):
        return read_json(self.path,{'groups':[]})

    def save(self,value):
        if self.state()!=value:
            atomic_json(self.path,value);self.c.invalidate()

    @staticmethod
    def members(state):
        return {int(fid):(g,inside) for g in state['groups'] for fid,inside in g['members'].items()}

    def discover(self,rows,attachments,state=None):
        state=copy.deepcopy(self.state() if state is None else state)
        # SQLite can reuse a deleted file's row id. An old group must never
        # claim a newly imported, unrelated VAR solely because its id matches.
        by_id={r['id']:r for r in rows}
        live_assets={(a['area'],a['rel'].casefold()) for a in attachments}
        groups=[]
        for g in state['groups']:
            main=by_id.get(g['main_id'])
            if not main or main['pkgkey']!=g['owner']:continue
            g['members']={fid:inside for fid,inside in g['members'].items()
                          if int(fid) in by_id and PurePosixPath(by_id[int(fid)]['rel']).name.casefold()==PurePosixPath(inside).name.casefold()}
            if str(g['main_id']) not in g['members']:continue
            g['attachments']=[a for a in g['attachments'] if (a['area'],a['rel'].casefold()) in live_assets]
            groups.append(g)
        state['groups']=groups
        assigned=set(self.members(state))
        assigned_aux={(a['area'],a['rel'].casefold()) for g in state['groups'] for a in g['attachments']}
        directories=defaultdict(list);auxdirs=defaultdict(list)
        generic=set(CATEGORIES)|{'人物卡','收藏','基础包','自定义包','包管家导入','包管家分类','收藏新增',
            '依赖包','场景人物包','服装头发环境包','资产环境包','包管家补充'}
        for r in rows:
            if r['id'] in assigned or r['rel'].startswith('基础包/'):continue
            for parent in PurePosixPath(r['rel']).parents:
                if str(parent)=='.':break
                if parent.name not in generic:directories[(r['area'],str(parent))].append(r)
        for a in attachments:
            if (a['area'],a['rel'].casefold()) in assigned_aux:continue
            for parent in PurePosixPath(a['rel']).parents:
                if str(parent)=='.':break
                auxdirs[(a['area'],str(parent))].append(a)
        # Prefer the original outer resource folder to its Models/Images subdirs.
        for (area,folder),files in sorted(directories.items(),key=lambda kv:(len(PurePosixPath(kv[0][1]).parts),kv[0])):
            if any(r['id'] in assigned for r in files):continue
            assets=[a for a in auxdirs[(area,folder)] if (a['area'],a['rel'].casefold()) not in assigned_aux]
            direct=[r for r in files if str(PurePosixPath(r['rel']).parent)==folder]
            matched=[r for r in files if r['pkgid'].casefold()==PurePosixPath(folder).name.casefold()]
            if not assets and not (matched and len(files)>1):continue
            if not direct and not matched and PurePosixPath(folder).parent.name not in generic:continue
            candidates=direct or files
            main=(matched or candidates)[0]
            if not matched and len(direct)>1:
                # Prefer a root scene/appearance over its bundled dependencies.
                import json
                main=min(candidates,key=lambda r:(not json.loads(r['info']).get('counts',{}).get('appearance'),
                                             not json.loads(r['info']).get('counts',{}).get('scene'),r['pkgkey']))
            name=main['pkgid'] if matched or len(files)==1 else PurePosixPath(folder).name
            group={'id':str(main['id']),'owner':main['pkgkey'],'main_id':main['id'],'name':name,
                'members':{str(r['id']):str(PurePosixPath(r['rel']).relative_to(folder)) for r in files},
                'attachments':[dict(a,inside=str(PurePosixPath(a['rel']).relative_to(folder))) for a in assets]}
            state['groups'].append(group);assigned.update(r['id'] for r in files)
            assigned_aux.update((a['area'],a['rel'].casefold()) for a in assets)
        return state

    def relocate(self,moves,reverse=False):
        if not self.path.exists():return
        state=self.state();before,after=('to','from') if reverse else ('from','to')
        moved={(m[before+'_area'],m[before+'_rel']):m for m in moves if m.get('auxiliary')}
        for g in state['groups']:
            for a in g['attachments']:
                m=moved.get((a['area'],a['rel']))
                if m:a.update(area=m[after+'_area'],rel=m[after+'_rel'])
        self.save(state)

    def rename_prefix(self,old,new):
        if not self.path.exists():return
        state=self.state()
        for group in state['groups']:
            for attachment in group['attachments']:
                rel=attachment['rel']
                if attachment['area'] in ('active','vault') and self.c.in_folder(rel,old):
                    attachment['rel']=new+rel[len(old):]
        self.save(state)

    @staticmethod
    def target(group,owner,favorite_root,favorite=None):
        favorite=owner['favorite'] if favorite is None else favorite
        return '/'.join(([favorite_root] if favorite else [])+[owner['category'],group['name']])

    def annotate(self,packages):
        state=self.state()
        owners={r['id']:p for p in packages.values() for r in p['files']}
        for p in packages.values():
            p['resource_folders']=[]
        for g in state['groups']:
            for key in {owners[int(fid)]['key'] for fid in g['members'] if int(fid) in owners}:
                packages[key]['resource_folders'].append({'name':g['name'],'owner':g['owner'],'main_id':g['main_id']})
