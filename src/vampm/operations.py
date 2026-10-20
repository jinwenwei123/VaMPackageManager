import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .activation import ActivationChoices
from .base_packages import BasePackages
from .bundles import Bundles
from .layout import package_path, auxiliary_rows
from .file_moves import move_no_replace, recover_move
from .util import (atomic_json, fingerprint, read_json, require_game_closed,
                   safe_path, sha256, stamp)


class Operations:
    def __init__(self, catalog):
        self.c = catalog
        self.directory = self.c.data / 'operations'
        self.directory.mkdir(exist_ok=True)
        self.plans = {}
        self.activation = ActivationChoices(catalog)
        self._history = {}
        self._history_lock = threading.Lock()
        self.launch_path=self.c.data/'launch-settings.json'
        self.base=BasePackages(catalog)
        self.bundles=Bundles(catalog)

    def history(self):
        rows = []
        for path in self.directory.glob('*/manifest.json'):
            try:
                version = fingerprint(path)
                with self._history_lock:
                    cached = self._history.get(str(path))
                    if not cached or cached[0] != version:
                        m = read_json(path)
                        cached = (version, {k:v for k,v in m.items() if k not in ('moves','keeps','preview','files','favorite_changes','origin_changes','previous_operations','profile_snapshot','activation_snapshot','policy_snapshot','activation_request','copy_choices','copy_conflicts','layout_snapshot','auto_sync_snapshot','mode_snapshot','base_snapshot','base_changes','base_resolved','bundle_snapshot','bundle_discovered','name_repair_state','json_repair_state')})
                        self._history[str(path)] = cached
                    rows.append(cached[1])
            except (OSError, ValueError): pass
        return sorted(rows, key=lambda r:r['created'], reverse=True)

    def check_pending(self, *, allow_storage=False):
        if not allow_storage: self.c.storage.check_pending()
        if (self.c.data / 'library-path-change.json').exists():
            raise ValueError('停用库路径变更待恢复，请关闭管家全部网页并重新打开；已有资源保留。')
        if any(h['status'] in ('running','interrupted','undoing') for h in self.history()):
            raise ValueError('有未完成的文件操作，请先到「操作记录」恢复该操作。')

    def plan(self, kind, keys, job, folder=None, profile_id=None,
             duplicate_mode=None, copy_choices=None, remember_copies=False, archive_scope=None):
        if archive_scope is not None and (kind != 'organize' or archive_scope not in ('all', 'favorites')
                                          or keys or folder or profile_id):
            raise ValueError('一键归档需指定全部资源或全部收藏，不能与包选择或其他操作混用。')
        if type(remember_copies) is not bool:
            raise ValueError('记住副本选择必须为布尔值')
        if kind != 'activate' and (duplicate_mode is not None or copy_choices is not None or remember_copies):
            raise ValueError('同名副本选项仅用于按需启用')
        self.check_pending()
        require_game_closed()
        job.message='更新文件状态，确保预览包含全部包'
        self.c.scan(job)
        profile = None
        if profile_id:
            if kind!='activate' or keys:raise ValueError('启动方案 id 仅用于 activate，不能与包选择混用')
            profile=next((p for p in read_json(self.c.data/'profiles.json',[]) if p['id']==profile_id),None)
            if not profile:raise ValueError('启动方案不存在')
            keys=self._profile_keys(profile)
        pkgs = self.c.packages()
        if archive_scope is not None:
            keys = [key for key, package in pkgs.items() if archive_scope == 'all' or package['favorite']]
            if not keys:
                raise ValueError('收藏夹中暂无可归档的包。' if archive_scope == 'favorites' else '资源库中暂无可归档的包。')
        favorite_root=self.c.favorite_folder()
        keys = list(dict.fromkeys(str(k).casefold() for k in keys))
        unknown = [k for k in keys if k not in pkgs]
        if unknown: raise ValueError('部分包已不在索引中，请重新扫描。')
        token = uuid.uuid4().hex
        plan = {'id':token,'kind':kind,'created':stamp(),'moves':[], 'missing':[], 'warnings':[], 'keeps':[],
                'signature':self.c.signature()}
        if archive_scope is not None:
            plan.update(archive_scope=archive_scope, archive_packages=len(keys))
        base_before=self.base.state()
        fixed_before=self.base.resolve(pkgs,base_before)
        fixed=fixed_before
        plan['base_snapshot']=base_before
        plan['base_resolved']=sorted(fixed_before['keys'])
        all_rows=self.c.rows();row_index={r['id']:r for r in all_rows}
        attachments=list(auxiliary_rows(self.c,job))
        bundle_before=self.bundles.state()
        bundle_state=self.bundles.discover(all_rows,attachments)
        members=self.bundles.members(bundle_state)
        bundle_aux={(a['area'],a['rel']):(g,a) for g in bundle_state['groups'] for a in g['attachments']}
        plan['bundle_snapshot']=bundle_before
        plan['bundle_discovered']=bundle_state
        touched={g['id'] for fid,(g,_) in members.items() if fid in row_index and row_index[fid]['pkgkey'] in keys
                 and row_index[fid]['pkgkey'] not in fixed['keys']}
        if kind in ('favorite','unfavorite','organize','move'):
            keys=list(dict.fromkeys(keys+[row_index[int(fid)]['pkgkey'] for g in bundle_state['groups'] if g['id'] in touched
                  for fid in g['members'] if int(fid) in row_index and row_index[int(fid)]['pkgkey'] not in fixed['keys']]))
        keyset=set(keys)
        favorite_overrides={k:kind=='favorite' for k in keys} if kind in ('favorite','unfavorite') else {}
        bundle_root=self.c.normalize_folder(folder) if folder and kind in ('favorite','unfavorite') else favorite_root
        def group_root(g):
            owner=pkgs.get(g['owner'])
            if not owner:return None
            root=self.bundles.target(g,owner,bundle_root,favorite_overrides.get(g['owner']))
            if kind=='move' and g['id'] in touched:root=self.c.normalize_folder(folder)+'/'+g['name']
            if sum(other['name'].casefold()==g['name'].casefold() for other in bundle_state['groups'])>1:
                root+='/副本-'+g['id']
            return root
        if kind in ('pin','unpin'):
            if not keys:raise ValueError('请选择需要固定或解除固定的包。')
            if kind=='unpin' and any(k not in fixed_before['roots'] for k in keys):
                raise ValueError('所选包包含自动保留的基础依赖，请先在详情中查看并解除其上游包的固定。')
            roots=set(base_before['roots'])
            roots.update(keys) if kind=='pin' else roots.difference_update(keys)
            after={'roots':sorted(roots)}
            fixed=self.base.resolve(pkgs,after)
            plan['base_changes']={'before':base_before,'after':after}
            plan['pin_count']=len(fixed['keys'])
            plan['missing']=fixed['missing']
        moves = plan['moves']
        planned_ids=set()
        reserved = {(r['area'], r['rel'].casefold()) for r in self.c.rows()}
        def add(row, area, rel, digest=None, bundled=False):
            if row.get('auxiliary') and self.base.inside(row['rel']) and not bundled:return
            if row.get('auxiliary') and (row['area'],row['rel']) in bundle_aux and not bundled:return
            if row.get('auxiliary') and kind in ('stage_all','organize') and not bundled:
                rel='/'.join('人物' if s=='人物卡' else s for s in rel.split('/'))
            membership=members.get(row.get('id'))
            if membership and kind not in ('dedup','sync_base'):
                g,inside=membership
                if group_root(g):rel=group_root(g)+'/'+inside
            if row.get('pkgkey') in fixed['keys']:
                area,rel='active',self.base.target(pkgs[row['pkgkey']],row)
                if membership and membership[0]['owner'] in fixed['keys'] and not self.base.inside(row['rel']):
                    rel='基础包/'+membership[0]['name']+'/'+membership[1]
            if row['area']==area and row['rel']==rel: return
            if not row.get('auxiliary') and row['id'] in planned_ids:return
            p = self.c.path(row)
            if fingerprint(p)!=[row['size'],row['mtime']]:
                raise ValueError('文件变化，请先重新扫描：'+row['rel'])
            dest = safe_path(self.c.roots[area],rel)
            if kind in ('activate','restore_all','organize','favorite','unfavorite','stage_all','sync_favorites','sync_activation','pin','unpin','sync_base'):
                # Reserve occupied paths even when their files will move later.
                # This avoids order-dependent swaps and keeps every VAR basename.
                original = Path(rel)
                suffix = 0
                while (area, rel.casefold()) in reserved or dest.exists():
                    label = str(row['id'] or uuid.uuid4().hex[:8]) + ('-'+str(suffix) if suffix else '')
                    if not row.get('auxiliary'):
                        rel = (original.parent / original.stem / ('副本-'+label) / original.name).as_posix()
                    else:rel = (original.parent / '附件副本' / label / original.name).as_posix()
                    dest = safe_path(self.c.roots[area],rel)
                    suffix += 1
                reserved.add((area, rel.casefold()))
            if dest.exists(): raise ValueError('目标路径已有文件，请处理重名后重试：'+str(dest))
            moves.append({'from_area':row['area'],'from_rel':row['rel'],'to_area':area,'to_rel':rel,
                          'size':row['size'],'mtime':row['mtime'],'sha':digest or '', 'row_id':row['id'],
                          **({'auxiliary':True} if row.get('auxiliary') else {})})
            if not row.get('auxiliary'):planned_ids.add(row['id'])

        if kind=='sync_base':
            plan['warnings']=['已固定包及新找到的可用依赖自动归入基础包，保持启用。']
        elif kind in ('pin','unpin'):
            mode=self.current_mode()
            snapshot,closure=self.current_selection(job,bundle_state)
            plan['mode_snapshot']=snapshot
            needed=None if snapshot['kind']=='restore_all' else {r['id'] for rows in closure['selected'].values() for r in rows}
            for key in fixed_before['keys']-fixed['keys']:
                for row in pkgs[key]['files']:
                    area=row['area'] if needed is None else ('active' if row['id'] in needed else 'vault')
                    add(row,area,package_path(pkgs[key],row,favorite_root))
            plan['layout_snapshot']=self._layout_snapshot(list(pkgs))
            plan['launch_snapshot']=mode.get('id')
            plan['warnings']=['固定包及可用依赖常驻 AddonPackages/基础包，分类与收藏仍可检索，按需方案和分类归档不移动它们。',
                              '解除固定后仍被其他固定包依赖的资源继续保留；其余包按当前启动方案和主分类存放。']
        elif kind in ('favorite','unfavorite','sync_favorites','sync_activation'):
            if not keys and kind in ('favorite','unfavorite'):raise ValueError('请选择需要收藏或取消收藏的包')
            if kind=='sync_favorites' and not self.follows_favorites():raise ValueError('当前未启用默认收藏方案，无需同步')
            root=self.c.normalize_folder(folder or favorite_root)
            if root=='基础包' or self.base.inside(root):raise ValueError('基础包目录不能用作收藏目录。')
            old_root=favorite_root;origins=self.c.favorite_origins();enabled=kind=='favorite'
            plan['favorite_changes']={k:{'before':pkgs[k]['favorite'],'after':enabled} for k in keys}
            plan['origin_changes']={};plan['favorite_count']=len(keys);plan['folder']=root
            if folder:plan['favorite_root']={'before':old_root,'after':root}
            overrides={k:enabled for k in keys}
            snapshot,closure=self.current_selection(job,bundle_state,overrides)
            chosen=None if snapshot['kind']=='restore_all' else {r['id'] for rows in closure['selected'].values() for r in rows}
            plan['mode_snapshot']=snapshot
            plan['missing']=closure['missing'];plan['keeps']=closure['keeps'];plan['auto_sync']=True
            plan['warnings'].append('按当前已应用方案同步启停；未应用方案时仅保留固定基础包，明确恢复全部启用时保留全部。')
            if closure['unresolved']:
                plan['warnings'].append('新增的未选定同名副本暂保留各份可读取内容；可在启动方案预览中指定副本。')
            affected=list(pkgs)
            for key in affected:
                p=pkgs[key];fav=overrides.get(key,p['favorite'])
                for r in pkgs[key]['files']:
                    fid=str(r['id']);old_origin=origins.get(fid)
                    area='active' if chosen is None or r['id'] in chosen else 'vault'
                    target=package_path(p,r,root,fav) if key in keyset or area!=r['area'] else r['rel']
                    add(r,area,target)
                    if key in keyset:plan['origin_changes'][fid]={'before':old_origin,'after':None}
            plan['layout_snapshot']=self._layout_snapshot(affected)
            plan['warnings'].append('收藏和普通资源分别在同名主分类目录存放，可从操作记录恢复。')
            if self.follows_favorites():
                for r in attachments:
                    if self.c.in_folder(r['rel'],root):add(r,'active' if snapshot['keys'] else 'vault',r['rel'])
        elif kind == 'dedup':
            audit = read_json(self.c.data/'duplicate-audit.json')
            if not audit: raise ValueError('请先运行内容校验。')
            if audit.get('signature')!=plan['signature']: raise ValueError('文件库已变化，请重新校验重复包。')
            rows = {r['id']:r for r in self.c.rows()}
            # Conservative: skip every same-ID conflict, even its equal subgroups.
            for group in audit['groups']:
                if group['conflict']: continue
                if group['pkgid'].casefold() in fixed['keys']:continue
                if keys and group['pkgid'].casefold() not in keys: continue
                keep = group['keep']
                r = rows.get(keep['id'])
                if not r or fingerprint(self.c.path(r)) != [keep['size'],keep['mtime']]:
                    raise ValueError('校验结果过期，请重新校验。')
                if sha256(self.c.path(r),job)!=group['sha']:
                    raise ValueError('保留副本内容已变动，请重新校验。')
                plan['keeps'].append(keep)
                for extra in group['extras']:
                    row = rows.get(extra['id'])
                    if not row or row['area']!=extra['area'] or row['rel']!=extra['rel']:
                        raise ValueError('文件位置改变，请重新校验。')
                    add(row,'quarantine',token+'/'+extra['area']+'/'+extra['rel'],group['sha'])
                    moves[-1]['keeper']=keep
            plan['warnings'] = [f"同名内容冲突的 {len(audit['conflicts'])} 组全部保留。", '不同版本不会合并；副本移到隔离区，可恢复。']
        elif kind == 'move':
            if not keys: raise ValueError('请选择需要转移的包。')
            if not folder: raise ValueError('请输入 AddonPackages 内的目标文件夹。')
            folder=self.c.normalize_folder(folder)
            if folder=='基础包' or self.base.inside(folder):raise ValueError('请使用固定基础包功能移入基础包目录。')
            for key in keys:
                if key in fixed['keys']:raise ValueError('固定基础包不能转移，请先解除固定。')
                root=favorite_root
                if pkgs[key]['favorite'] and folder.casefold()!=root.casefold() and not self.c.in_folder(folder+'/',root):
                    raise ValueError('收藏包请移动到收藏目录内；需要移出时先取消收藏：'+root)
                for r in pkgs[key]['files']:
                    add(r,r['area'],folder+'/'+r['rel'].rsplit('/',1)[-1])
            plan['folder']=folder
            plan['warnings']=['转移到所选库中的指定文件夹；保留文件名和启用状态，可恢复。']
        elif kind in ('organize','stage_all'):
            if kind=='stage_all':keys=list(pkgs);keyset=set(keys)
            if not keys: raise ValueError('请选择需要归档的包。')
            chosen=set()
            if kind=='organize':
                snapshot,closure=self.current_selection(job,bundle_state)
                plan['mode_snapshot']=snapshot;plan['missing']=closure['missing'];plan['keeps']=closure['keeps']
                chosen=None if snapshot['kind']=='restore_all' else {r['id'] for rows in closure['selected'].values() for r in rows}
            for key in pkgs:
                p = pkgs[key]
                for r in p['files']:
                    area='active' if chosen is None or r['id'] in chosen else 'vault'
                    target=package_path(p,r,favorite_root) if key in keyset or area!=r['area'] else r['rel']
                    add(r,area,target)
            plan['layout_snapshot']=self._layout_snapshot(list(pkgs))
            plan['warnings'] = ['按主分类实际存放，每个包仍可同时显示在多个内容分类中；收藏在收藏目录内单独分类。',
                                '同名包放在 包名/副本编号/ 内，保留全部原始文件；所有转移均可恢复。']
            if kind=='organize':plan['warnings'].append('同时按当前方案校正全库启停；未应用方案时仅保留固定基础包。')
            if kind=='organize' and closure['unresolved']:
                plan['warnings'].append('新增的未选定同名副本暂保留各份可读取内容；可在启动方案预览中指定副本。')
            if kind=='stage_all':
                for r in attachments:
                    add(r,'vault',r['rel'])
                plan['warnings'].append('固定基础包常驻；其余包全部停用。图片、说明等附件保留相对目录一并移入停用库。')
        elif kind == 'activate':
            if not keys and not profile: raise ValueError('请至少选择一个包。')
            saved_policy = self.activation.policy(profile_id)
            policy = self.activation.validate(duplicate_mode if duplicate_mode is not None else saved_policy['mode'],
                                              copy_choices if copy_choices is not None else saved_policy['choices'])
            closure = self.activation.resolve(keys, policy, job,bundle_state)
            plan['missing'] = closure['missing']
            plan['selected'] = len(keys)
            plan['included'] = len(closure['keys'])
            plan['included_files'] = sum(len(rows) for rows in closure['selected'].values())
            plan['duplicate_mode'] = policy['mode']
            plan['copy_choices'] = closure['policy']['choices']
            plan['copy_conflicts'] = closure['conflicts']
            plan['unresolved_copies'] = closure['unresolved']
            plan['activation_request'] = {'kind':'activate', **({'profile_id':profile_id} if profile_id else {'keys':keys})}
            plan['remember_copies'] = remember_copies if duplicate_mode is not None or copy_choices is not None else True
            plan['choices_saved'] = remember_copies
            plan['keeps'] = closure['keeps']
            plan['activation_snapshot'] = self._activation_snapshot(keys, closure)
            if profile:
                plan['profile_id']=profile['id']
                plan['profile_name']=profile['name']
                plan['profile_snapshot']=self._profile_snapshot(profile,keys,closure)
                if profile.get('source',{}).get('type')=='folder':
                    plan['folder_membership']={'profile_id':profile['id'],'folder':profile['source']['folder'],'keys':sorted(keys)}
            chosen_ids = set()
            if not closure['unresolved']:
                for i,key in enumerate(closure['keys']):
                    for row in closure['selected'][key]:
                        chosen_ids.add(row['id'])
                        add(row,'active',package_path(pkgs[key],row,favorite_root))
                    job.update(i+1,len(closure['keys']),pkgs[key]['pkgid'])
                for row in self.c.rows():
                    if row['id'] not in chosen_ids:
                        add(row,'vault',package_path(pkgs[row['pkgkey']],row,favorite_root))
                plan['layout_snapshot']=self._layout_snapshot(list(pkgs))
                for r in attachments:
                    if self.c.in_folder(r['rel'],favorite_root):
                        add(r,'active' if profile_id==self.default_profile_id() else 'vault',r['rel'])
            plan['warnings'] = ['已自动递归加入依赖；未选包放入本工具的停用库，不删除。',
                                '会话插件和本地未打包场景不在 VAR 依赖图中，请将它们使用的包加入方案。']
            if policy['mode'] == 'all' and closure['conflicts']:
                plan['warnings'].append('全部保留会将各份同名文件放在不同目录；VaM 对同一 UID 只采用一份，顺序不保证，也不会合并内容。')
                if any(not v['valid'] for group in closure['conflicts'] for v in group['variants']):
                    plan['warnings'].append('全部保留中包含不可读取的副本；文件会保留，依赖仅按可读取的副本计算。')
            if any(not pkgs[k]['deep'] for k in closure['keys']):
                plan['warnings'].append('部分包只完成快速扫描；建议先深度扫描以补充未声明的文本引用。')
            # Remembering the policy is separate from moving files, including
            # previews blocked by missing dependencies or needing no moves.
            if remember_copies:
                self.activation.save(closure['policy'], profile_id)
            plan['policy_snapshot'] = self.activation.policy(profile_id)
        elif kind == 'restore_all':
            for r in self.c.rows():
                add(r,'active',package_path(pkgs[r['pkgkey']],r,favorite_root))
            for r in attachments:
                if r['area']=='vault':add(r,'active',r['rel'])
        else:
            raise ValueError('未知操作')
        for key in fixed['keys']:
            for row in pkgs[key]['files']:add(row,'active',self.base.target(pkgs[key],row))
        if kind!='dedup':
            planned={m['row_id']:m for m in moves if not m.get('auxiliary')}
            for g in bundle_state['groups']:
                main=row_index.get(g['main_id'])
                if not main:continue
                if not (g['main_id'] in planned or g['id'] in touched or kind in ('stage_all','activate','restore_all','sync_favorites','sync_activation')):continue
                dest=planned.get(g['main_id'])
                area=dest['to_area'] if dest else main['area']
                rel=dest['to_rel'] if dest else main['rel']
                inside=g['members'][str(main['id'])]
                root=rel[:-len(inside)].rstrip('/') if rel.endswith('/'+inside) else group_root(g)
                for a in g['attachments']:
                    if safe_path(self.c.roots[a['area']],a['rel']).is_file():
                        add(dict(a,id=None,auxiliary=True),area,root+'/'+a['inside'],bundled=True)
            # Loose leftovers from the former category name use the same label.
            for a in attachments:
                if (a['area'],a['rel']) in bundle_aux or '人物卡' not in a['rel'].split('/'):continue
                if kind not in ('stage_all','organize'):continue
                if any(m.get('auxiliary') and m['from_area']==a['area'] and m['from_rel']==a['rel'] for m in moves):continue
                add(a,'vault' if kind=='stage_all' else a['area'],'/'.join('人物' if s=='人物卡' else s for s in a['rel'].split('/')))
        fixed_ids={r['id'] for k in fixed['keys'] for r in pkgs[k]['files']}
        mode_snapshot=plan.get('mode_snapshot',{})
        if mode_snapshot.get('source',{}).get('type')=='folder':
            plan['folder_membership']={'profile_id':mode_snapshot['profile_id'],
                                      'folder':mode_snapshot['source']['folder'],'keys':mode_snapshot['keys']}
        if 'base_changes' not in plan and any(m['row_id'] in fixed_ids
                                             for m in moves if not m.get('auxiliary')):
            plan['base_changes']={'before':base_before,'after':base_before}
            plan['launch_snapshot']=self.current_mode().get('id')
        plan['fixed_packages']=len(fixed['keys'])
        if fixed['keys']:
            plan['warnings'].append(f"{len(fixed['keys'])} 个固定基础包及依赖保持启用；已在基础包中的文件保持原位。")
        targets = [(m['to_area'],m['to_rel'].casefold()) for m in moves]
        if len(set(targets))!=len(targets): raise ValueError('计划存在目标重名，无法执行。')
        plan['count'] = len(moves)
        plan['bytes'] = sum(m['size'] for m in moves)
        plan['attachment_count']=sum(bool(m.get('auxiliary')) for m in moves)
        self.plans[token] = plan
        return self.public(plan)

    @staticmethod
    def public(plan):
        return {**{k:v for k,v in plan.items() if k not in ('moves','keeps','favorite_changes','origin_changes','profile_snapshot','activation_snapshot','policy_snapshot','layout_snapshot','auto_sync_snapshot','mode_snapshot','base_snapshot','base_changes','base_resolved','bundle_snapshot','bundle_discovered')},
                'preview':plan['moves'][:80]}

    def _path(self, m, prefix):
        if m[prefix+'_area'] == 'quarantine' and self.c.storage.value['locations']['quarantine'] == 'game':
            self.c.storage.ensure_base()
        return safe_path(self.c.roots[m[prefix+'_area']],m[prefix+'_rel'])

    @staticmethod
    def _event(path, **value):
        Operations._events(path, [value])

    def _prune_empty_sources(self,moves,prefix='from'):
        parents=set()
        for m in moves:
            root=self.c.roots[m[prefix+'_area']]
            parent=self._path(m,prefix).parent
            while parent!=root:
                parents.add(parent);parent=parent.parent
        for parent in sorted(parents,key=lambda p:len(p.parts),reverse=True):
            try:parent.rmdir()
            except OSError:pass

    @staticmethod
    def _events(path, values):
        # The whole batch's intent is durable BEFORE any rename. Recovery checks actual
        # paths, so a crash between renames also safely handles unexecuted intentions.
        with path.open('a',encoding='utf-8') as f:
            for value in values:
                f.write(json.dumps(dict(time=stamp(),**value),ensure_ascii=False)+'\n')
            f.flush()
            os.fsync(f.fileno())

    def apply(self, token, job):
        self.check_pending()
        require_game_closed()
        plan = self.plans.pop(token,None)
        if not plan: raise ValueError('预览已过期，请重新生成。')
        if plan.get('unresolved_copies'): raise ValueError('仍有同名副本待选择，请指定副本或选择全部保留，再更新预览。')
        if plan['kind']=='activate' and plan['selected'] and not plan['included_files']:
            raise ValueError('所选包没有可读取的副本，请先选择至少一个可用的包。')
        job.message='再次检查文件状态'
        self.c.scan(job)
        if self.c.signature()!=plan['signature']: raise ValueError('预览后文件库已变化，请重新生成预览。')
        if self.bundles.state()!=plan['bundle_snapshot']:raise ValueError('资源文件夹关联已变化，请重新预览。')
        if self.base.state()!=plan['base_snapshot']:raise ValueError('固定基础包设置已变化，请重新预览。')
        if sorted(self.base.resolve(self.c.packages(),self.base.state())['keys'])!=plan['base_resolved']:
            raise ValueError('固定基础包的依赖分析已变化，请重新预览。')
        if plan.get('layout_snapshot')!=None and self._layout_snapshot(list(plan['layout_snapshot']))!=plan['layout_snapshot']:
            raise ValueError('主分类或收藏内容已更新，或副本已变化，请重新预览。')
        if plan.get('base_changes') and self.current_mode().get('id')!=plan['launch_snapshot']:
            raise ValueError('当前启动方案已变化，请重新预览固定操作。')
        if plan.get('mode_snapshot'):
            overrides={k:v['after'] for k,v in plan.get('favorite_changes',{}).items()}
            actual,_=self.current_selection(job,plan['bundle_discovered'],overrides)
            if actual!=plan['mode_snapshot']:
                raise ValueError('当前启动方案、来源包或依赖已变化，请重新扫描或预览。')
        if plan.get('auto_sync_snapshot'):
            sync=plan['auto_sync_snapshot']
            if self.current_mode().get('id')!=plan['launch_snapshot'] or self.activation.policy(self.default_profile_id())!=sync['policy']:
                raise ValueError('当前启动方案或副本选择已变化，请重新操作。')
            actual=self.activation.resolve(sync['keys'],sync['policy'],job,plan['bundle_discovered'])
            if self._activation_snapshot(sync['keys'],actual)!=sync['closure']:raise ValueError('收藏依赖已变化，请重新操作。')
        if plan['kind'] == 'activate':
            if self.activation.policy(plan.get('profile_id')) != plan['policy_snapshot']:
                raise ValueError('记住的副本选择已更新，请重新预览后启用。')
            policy = {'mode':plan['duplicate_mode'], 'choices':plan['copy_choices']}
            keys = plan['activation_request'].get('keys', [])
        if plan.get('profile_id'):
            profile=next((p for p in read_json(self.c.data/'profiles.json',[]) if p['id']==plan['profile_id']),None)
            if profile:
                keys=self._profile_keys(profile)
                closure=self.activation.resolve(keys,policy,job,plan['bundle_discovered'])
                snapshot=self._profile_snapshot(profile,keys,closure)
            if not profile or snapshot!=plan['profile_snapshot']:
                raise ValueError('启动方案或收藏内容已更新，请重新预览后启用，确保包含最新的包及依赖。')
        elif plan['kind'] == 'activate':
            closure=self.activation.resolve(keys,policy,job,plan['bundle_discovered'])
        if plan['kind'] == 'activate' and self._activation_snapshot(keys,closure)!=plan['activation_snapshot']:
            raise ValueError('副本或依赖分析已更新，请重新预览后启用。')
        for k in plan['keeps']:
            p=safe_path(self.c.roots[k['area']],k['rel'])
            if not p.exists() or fingerprint(p)!=[k['size'],k['mtime']] or sha256(p,job)!=k['sha']:
                raise ValueError('保留副本已变化，清理计划已停止。请重新扫描并校验。')
        for m in plan['moves']:
            if fingerprint(self._path(m,'from'))!=[m['size'],m['mtime']] or self._path(m,'to').exists():
                raise ValueError('文件状态变化，请重新扫描并预览。')
        folder = self.directory/token
        folder.mkdir()
        manifest = folder/'manifest.json'
        events = folder/'events.jsonl'
        state = {**plan,'status':'running','completed':0}
        atomic_json(manifest,state)
        moved = []
        checked = time.monotonic()
        try:
            # Freeze implicit adoption before the first rename so interruption
            # recovery cannot mistake newly moved dependencies for manual roots.
            if not self.base.path.exists():self.base.save(plan['base_snapshot'])
            self.bundles.save(plan['bundle_discovered'])
            for i,m in enumerate(plan['moves']):
                job.check()
                if time.monotonic() - checked >= 1:
                    require_game_closed()
                    checked = time.monotonic()
                if i % 64 == 0:
                    self._events(events, [{'index':n,'phase':'intent'}
                        for n in range(i,min(i+64,len(plan['moves'])))])
                src, dst = self._path(m,'from'), self._path(m,'to')
                if fingerprint(src)!=[m['size'],m['mtime']]: raise ValueError('源文件已变动：'+m['from_rel'])
                if m.get('keeper'):
                    k=m['keeper']
                    keep=safe_path(self.c.roots[k['area']],k['rel'])
                    if not keep.exists() or fingerprint(keep)!=[k['size'],k['mtime']]:
                        raise ValueError('保留副本丢失或改变，已停止清理。')
                if m['sha'] and sha256(src,job)!=m['sha']: raise ValueError('副本校验不一致：'+m['from_rel'])
                move_no_replace(src,dst,job)
                moved.append(m)
                if (i+1) % 64 == 0 or i+1 == len(plan['moves']):
                    self._event(events,index=i,phase='batch_moved')
                state['completed'] = i+1
                job.update(i+1,len(plan['moves']),m['from_rel'])
            state['status'] = 'done'
            state['applied_at']=stamp()
            state['applied_ns']=time.time_ns()
            self.c.apply_favorite_metadata(state)
            self.base.save(plan.get('base_changes',{}).get('after',plan['base_snapshot']))
        except BaseException:
            state['status'] = 'interrupted'
            raise
        finally:
            try:
                self.bundles.relocate(moved)
                self.c.relocate(moved)
                self._prune_empty_sources(moved)
            except BaseException:
                state['status'] = 'interrupted'
                raise
            finally:
                atomic_json(manifest,state)
        return {'operation':token,'count':state['completed'],'kind':plan['kind'],
                'favorite_count':plan.get('favorite_count'),'auto_sync':plan.get('auto_sync',False),
                'missing_count':len(plan['missing']),'warnings':plan['warnings']}

    def undo(self, token, job):
        self.c.storage.check_pending()
        require_game_closed()
        if not isinstance(token,str) or not token.isalnum(): raise ValueError('无效记录')
        manifest = self.directory/token/'manifest.json'
        state = read_json(manifest)
        if not state or state['status']=='undone': raise ValueError('该记录不存在或已经恢复')
        if state.get('irreversible'):
            raise ValueError('此操作涉及永久清理的隔离文件，无法整批恢复；剩余文件可在重复清理页面查看。')
        if state['kind']=='repair_json':
            from .json_repair import JsonRepairs
            return JsonRepairs(self.c,self).undo(token,job)
        if state['kind']=='repair_names':
            from .name_repair import NameRepairs
            return NameRepairs(self.c,self).undo(token,job)
        if state.get('base_changes'):
            if self.base.state() not in (state['base_changes']['before'],state['base_changes']['after']):
                raise ValueError('请先恢复较新的固定基础包设置。')
        else:
            pinned_ids={r['id'] for p in self.c.packages().values() if p['pinned'] for r in p['files']}
            if any(m.get('row_id') in pinned_ids and (m['from_area']!='active' or not self.base.inside(m['from_rel']))
                   for m in state.get('moves',[])):
                raise ValueError('恢复会移动当前固定的基础包，请先解除固定或恢复较新的固定操作。')
        if state['kind']=='rename_collection':
            previous=set(state['previous_operations'])|{token}
            if any(h['id'] not in previous and h['status']!='undone' and h['kind']!='purge_quarantine' for h in self.history()):
                raise ValueError('请先恢复重命名之后的文件操作，再恢复收藏目录名。')
            return self._rename_collection(state,manifest,job,reverse=True)
        # Recover exactly the moves whose intention was durably recorded.
        intended = set()
        events = manifest.parent/'events.jsonl'
        if events.exists():
            for line in events.read_text('utf-8').splitlines():
                try:
                    e = json.loads(line)
                    if e['phase']=='intent': intended.add(e['index'])
                except (ValueError, KeyError): pass
        moves, restored = [], []
        for i in sorted(intended,reverse=True):
            m = state['moves'][i]
            original, current = self._path(m,'from'), self._path(m,'to')
            recover_move(original, current, job)
            recover_move(current, original, job)
            if original.exists() and not current.exists():
                if fingerprint(original)!=[m['size'],m['mtime']]:
                    raise ValueError('原位置文件被修改，需手动检查：'+m['from_rel'])
                restored.append(m)
                continue
            if original.exists() or not current.exists():
                raise ValueError('恢复位置有冲突或文件缺失：'+m['from_rel']+'。请先恢复较新的操作。')
            if fingerprint(current)!=[m['size'],m['mtime']]:
                raise ValueError('移动后的文件被修改，需手动检查：'+m['to_rel'])
            moves.append((i,m,original,current))
        state['status']='undoing'
        atomic_json(manifest,state)
        checked = time.monotonic()
        try:
            for n,(i,m,original,current) in enumerate(moves,1):
                job.check()
                if time.monotonic() - checked >= 1:
                    require_game_closed()
                    checked = time.monotonic()
                if (n-1) % 64 == 0:
                    self._events(events,[{'index':v[0],'phase':'undo_intent'} for v in moves[n-1:n+63]])
                # Recheck reparse points and fingerprints immediately before the rename.
                original,current=self._path(m,'from'),self._path(m,'to')
                if fingerprint(current)!=[m['size'],m['mtime']]: raise ValueError('恢复时文件发生变化：'+m['to_rel'])
                move_no_replace(current,original,job)
                restored.append(m)
                if n % 64 == 0 or n==len(moves): self._event(events,index=i,phase='batch_undone')
                job.update(n,len(moves),m['from_rel'])
            state['status']='undone'
            self.c.apply_favorite_metadata(state,reverse=True)
            if state.get('base_changes'):self.base.save(state['base_changes']['before'])
            state['restored']=stamp()
        except BaseException:
            state['status']='interrupted'
            raise
        finally:
            try:
                self.bundles.relocate(restored,reverse=True)
                self.c.relocate(restored, reverse=True)
                self._prune_empty_sources(restored,'to')
            except BaseException:
                state['status'] = 'interrupted'
                raise
            finally:
                atomic_json(manifest,state)
        return {'restored':len(moves)}

    def favorite(self,keys,enabled,job,folder=None):
        plan=self.plan('favorite' if enabled else 'unfavorite',keys,job,folder)
        return self.apply(plan['id'],job)

    def rename_collection(self,folder,job):
        self.check_pending();require_game_closed();self.c.scan(job)
        old=self.c.favorite_folder();new=self.c.normalize_folder(folder)
        if new=='基础包' or self.base.inside(new):raise ValueError('基础包目录不能用作收藏目录。')
        if old.casefold()==new.casefold():raise ValueError('新旧收藏目录相同')
        if self.c.in_folder(new,old) or self.c.in_folder(old,new):
            raise ValueError('新旧收藏目录不能互相包含')
        directories=[]
        for area in ('active','vault'):
            root=self.c.roots[area];src=safe_path(root,old);dst=safe_path(root,new)
            if dst.exists():raise ValueError('目标目录已存在，不能覆盖：'+str(dst))
            if src.exists():
                if not src.is_dir():raise ValueError('收藏位置不是文件夹')
                directories.append({'area':area,'from_rel':old,'to_rel':new})
        if not directories:raise ValueError('当前收藏目录不存在')
        rows=[r for r in self.c.rows() if self.c.in_folder(r['rel'],old)]
        token=uuid.uuid4().hex;folder=self.directory/token;folder.mkdir()
        state={'id':token,'kind':'rename_collection','created':stamp(),'status':'running','completed':0,
               'count':len(rows),'bytes':sum(r['size'] for r in rows),'favorite_count':len(self.c.favorite_keys()),
               'folder':new,'favorite_root':{'before':old,'after':new},'directory_moves':directories,
               'previous_operations':[h['id'] for h in self.history()]}
        manifest=folder/'manifest.json';atomic_json(manifest,state)
        return self._rename_collection(state,manifest,job)

    def _rename_collection(self,state,manifest,job,reverse=False):
        before,after=('to','from') if reverse else ('from','to')
        old,new=(state['favorite_root'][s] for s in (('after','before') if reverse else ('before','after')))
        state['status']='undoing' if reverse else 'running';atomic_json(manifest,state)
        try:
            for i,m in enumerate(state['directory_moves']):
                job.check();require_game_closed()
                root=self.c.roots[m['area']]
                src=safe_path(root,m[before+'_rel']);dst=safe_path(root,m[after+'_rel'])
                recover_move(src,dst,job)
                recover_move(dst,src,job)
                # Already at the destination means the rename completed before an
                # interruption, or a forward step was never started during undo.
                if src.exists() and not dst.exists():
                    if not src.is_dir():raise ValueError('收藏位置不是文件夹')
                    self._event(manifest.parent/'events.jsonl',index=i,
                                phase='directory_undo_intent' if reverse else 'directory_intent')
                    move_no_replace(src,dst,job)
                elif src.exists() or not dst.is_dir():
                    raise ValueError('恢复位置冲突或收藏目录缺失，请核对新旧目录。')
                job.update(i+1,len(state['directory_moves']),m[after+'_rel'])
            self.bundles.rename_prefix(old,new)
            self.c.rename_collection_index(old,new)
            self.plans.clear()
            state['status']='undone' if reverse else 'done';state['completed']=state['count']
            if reverse:state['restored']=stamp()
        except BaseException:
            state['status']='interrupted'
            raise
        finally:atomic_json(manifest,state)
        return {'operation':state['id'],'kind':state['kind'],'count':state['count'],'folder':new}

    def adopt_favorites(self,folder,job,profile_name=None):
        folder=self.c.normalize_folder(folder)
        self.c.scan(job)
        keys=list(dict.fromkeys(self.c.favorite_keys()+self.c.folder_keys(folder)))
        if not keys:raise ValueError('所选文件夹没有已索引的 VAR')
        result=self.favorite(keys,True,job,folder)
        if profile_name:
            existing=next((p for p in self.profiles() if p['name']==profile_name and p.get('source',{}).get('type')=='favorites'),None)
            profiles=self.save_profile(profile_name,[],existing['id'] if existing else None,favorites=True)
            result['profile']=next(p for p in profiles if p['name']==profile_name and p.get('source',{}).get('type')=='favorites')
        return result

    def _profile_keys(self, profile):
        # Dynamic profiles always resolve their source; saved keys are only an
        # old snapshot and must never become the authoritative favorites list.
        source=profile.get('source',{})
        if source.get('type')=='favorites':keys=self.c.favorite_keys()
        elif source.get('type')=='folder':
            # Classification and activation may relocate a source directory.
            # Keep its enrolled package identities, while discovering new files
            # at the original source. Only completed, non-undone plans enroll.
            records=[h for h in self.history() if h['status']=='done'
                     and h.get('folder_membership',{}).get('profile_id')==profile['id']
                     and h['folder_membership']['folder']==source['folder']]
            latest=max(records,key=lambda h:(h.get('applied_at',h['created']),h.get('applied_ns',0))) if records else None
            enrolled=latest['folder_membership']['keys'] if latest else profile.get('keys',[])
            keys=self.c.folder_keys(source['folder'])+[k for k in self.c.canonical_keys(enrolled) if k in self.c.packages()]
        else:keys=profile.get('keys',[])
        return sorted(self.c.canonical_keys(keys))

    def _layout_snapshot(self,keys):
        packages=self.c.packages()
        return {k:{'category':packages[k]['category'],'favorite':packages[k]['favorite'],
                   'files':sorted(r['id'] for r in packages[k]['files'])} for k in keys if k in packages}

    def default_profile_id(self):
        return read_json(self.launch_path,{}).get('default_profile_id')

    def ensure_default_profile(self):
        profiles=read_json(self.c.data/'profiles.json',[])
        default=next((p for p in profiles if p['id']==self.default_profile_id() and p.get('source',{}).get('type')=='favorites'),None)
        if default:return default['id']
        default=next((p for p in profiles if p['name']=='基础收藏' and p.get('source',{}).get('type')=='favorites'),None)
        if not default:
            default={'id':uuid.uuid4().hex,'name':'基础收藏','keys':[],'source':{'type':'favorites'},'updated':stamp()}
            profiles.insert(0,default);atomic_json(self.c.data/'profiles.json',profiles)
            self.activation.save({'mode':'all','choices':{}},default['id'])
        atomic_json(self.launch_path,{'default_profile_id':default['id']})
        self.c.invalidate(packages=False)
        return default['id']

    def current_mode(self):
        relevant=[h for h in self.history() if h['kind'] in ('activate','restore_all','stage_all') and h['status']=='done']
        return max(relevant,key=lambda h:(h.get('applied_at',h['created']),
                   h.get('applied_ns') or (self.directory/h['id']/'manifest.json').stat().st_mtime_ns)) if relevant else {}

    def follows_favorites(self):
        mode=self.current_mode()
        return bool(self.default_profile_id() and mode.get('kind')=='activate' and mode.get('profile_id')==self.default_profile_id())

    def current_selection(self,job,bundle_state,overrides=None):
        """Resolve the applied mode, never a saved/previewed but unapplied profile.

        None/stage_all enables only fixed packages (added by the planner).
        restore_all is an explicit user choice and survives later scans.
        """
        mode=self.current_mode();kind=mode.get('kind','stage_all')
        keys=[];source={};policy={'mode':'select','choices':{}}
        pid=mode.get('profile_id')
        if kind=='activate':
            manifest=read_json(self.directory/mode['id']/'manifest.json',{})
            profile=next((p for p in read_json(self.c.data/'profiles.json',[]) if p['id']==pid),None) if pid else None
            if profile:
                source=profile.get('source',{})
                if source.get('type')=='favorites':
                    overrides=overrides or {}
                    keys=sorted(k for k,p in self.c.packages().items() if overrides.get(k,p['favorite']))
                else:keys=self._profile_keys(profile)
            elif not pid:keys=self.c.canonical_keys(manifest.get('activation_request',{}).get('keys',[]))
            # A deleted profile means no selected roots, rather than reviving
            # its stale snapshot. Use the *applied* copy policy, including
            # unsaved choices. Saving a different preview must not enable it.
            saved=self.activation.policy(pid)
            policy=self.activation.validate(manifest.get('duplicate_mode',saved['mode']),manifest.get('copy_choices',saved['choices']))
            policy['choices']={self.c.canonical_keys([k])[0]:v for k,v in policy['choices'].items()}
        closure=self.activation.resolve(keys,policy,job,bundle_state)
        snapshot={'mode_id':mode.get('id'),'kind':kind,'profile_id':pid,'source':source,
                  'keys':sorted(keys),'policy':policy,'closure':self._activation_snapshot(keys,closure)}
        return snapshot,closure

    def sync_activation(self,job):
        plan=self.plan('sync_activation',[],job)
        if not plan['count']:
            state=self.plans.pop(plan['id'])
            self.bundles.save(state['bundle_discovered'])
            return {'count':0,'auto_sync':True,'missing_count':len(plan['missing'])}
        return self.apply(plan['id'],job)

    def sync_favorites(self,job):
        if not self.follows_favorites():return None
        return self.sync_activation(job)

    def sync_base(self,job):
        packages=self.c.packages()
        if not any(p['pinned'] and any(r['area']!='active' or not self.base.inside(r['rel']) for r in p['files'])
                   for p in packages.values()):return None
        plan=self.plan('sync_base',[],job)
        return self.apply(plan['id'],job)

    @staticmethod
    def _profile_snapshot(profile, keys, closure):
        return {'source':profile.get('source',{}),'keys':sorted(keys),
                'included':closure['keys'],'missing':closure['missing']}

    @staticmethod
    def _activation_snapshot(keys, closure):
        return {'keys':sorted(keys), 'included':closure['keys'], 'missing':closure['missing'],
                'unresolved':closure['unresolved'],
                'files':{k:sorted(r['id'] for r in rows) for k,rows in closure['selected'].items()}}

    def profiles(self):
        result=[];current=self.current_mode();default_id=self.default_profile_id()
        for p in read_json(self.c.data/'profiles.json',[]):
            keys=self._profile_keys(p)
            policy=self.activation.policy(p['id'])
            closure=self.c._memo(('profile',p['id'],tuple(keys),json.dumps(policy,sort_keys=True)),
                                 lambda:self.activation.resolve(keys,policy))
            result.append(dict(p,keys=keys,is_default=p['id']==default_id,
                               is_active=current.get('kind')=='activate' and current.get('profile_id')==p['id'],
                               included_count=len(closure['keys']),missing_count=len(closure['missing']),
                               missing_preview=closure['missing'][:8],duplicate_mode=policy['mode'],
                               unresolved_copies=len(closure['unresolved'])))
        return sorted(result,key=lambda p:not p['is_default'])

    def save_profile(self, name, keys, profile_id=None,folder=None,favorites=False):
        name = str(name).strip()[:60]
        keys = list(dict.fromkeys(str(k).casefold() for k in keys))
        if sum([bool(keys),bool(folder),bool(favorites)])>1:raise ValueError('包选择、文件夹、全部收藏只能选一种方案来源')
        source={}
        if favorites:source={'type':'favorites'};keys=self.c.favorite_keys()
        elif folder:
            folder=self.c.normalize_folder(folder);source={'type':'folder','folder':folder};keys=self.c.folder_keys(folder)
        if not name or (not keys and not source): raise ValueError('请填写方案名并选择至少一个包。')
        if any(k not in self.c.packages() for k in keys): raise ValueError('选择中有不存在的包')
        if profile_id and profile_id==self.default_profile_id() and not favorites:raise ValueError('默认方案固定跟随全部收藏，请另存其他来源的方案。')
        profiles = read_json(self.c.data/'profiles.json',[])
        pid = profile_id or uuid.uuid4().hex
        profiles = [p for p in profiles if p['id']!=pid]
        profiles.append({'id':pid,'name':name,'keys':keys,'source':source,'updated':stamp()})
        atomic_json(self.c.data/'profiles.json',profiles)
        self.c.invalidate(packages=False)
        return self.profiles()

    def delete_profile(self, pid):
        if pid==self.default_profile_id():raise ValueError('基础收藏是默认方案，请保留；可切换到其他方案或全部停用。')
        profiles = [p for p in read_json(self.c.data/'profiles.json',[]) if p['id']!=pid]
        atomic_json(self.c.data/'profiles.json',profiles)
        self.activation.forget(pid)
        self.c.invalidate(packages=False)
        return profiles

    def launch(self):
        self.check_pending()
        require_game_closed()
        exe = self.c.game/'VaM.exe'
        if not exe.is_file(): raise ValueError('游戏根目录没有 VaM.exe。')
        if os.name!='nt': raise ValueError('请使用 Windows EXE 启动游戏。')
        subprocess.Popen([str(exe),'-vrmode','None'],cwd=self.c.game)
        return {'started':True}

    def import_target(self):
        """New imports must not silently expand an already applied activation set."""
        latest=self.current_mode()
        if latest:
            on_demand=latest['kind']!='restore_all'
            name=latest.get('profile_name') or '临时选择'
            reason='当前全部停用' if latest['kind']=='stage_all' else ('当前按需方案：'+name) if on_demand else '当前已恢复全部启用'
        else:
            on_demand=True
            reason='当前未应用方案，仅固定基础包启用'
        return {'area':'vault' if on_demand else 'active','on_demand':on_demand,'reason':reason}
