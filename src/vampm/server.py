import hmac
import json
import mimetypes
import os
import secrets
import shutil
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .catalog import Catalog
from .game_directories import GameDirectories, WorkspaceChanged
from .onboarding import Onboarding
from .home_layout import HomeLayout
from .quarantine import Quarantine
from . import library_paths
from .folder_picker import choose_folder
from .operations import Operations
from .importer import Importer, ASSET_EXTENSIONS, MAX_IMPORT_FILES
from .providers import AI, Downloads
from .agent import Agent
from .agent_tools import TOOLS, invoke
from .covers import Covers
from .summary_queue import SummaryQueue
from .lifecycle import PageSessions
from .appearance import Appearance
from .language import Language
from .library_preferences import LibraryPreferences
from .chat_presets import ChatPresets, text_field
from .name_repair import NameRepairs
from .package_repair import PackageRepairs
from .reveal import reveal_file
from .util import Jobs, read_json, safe_path, vam_running


class Application:
    def __init__(self, game, home, *, _host=None, _ident=None, _entry=None, _empty=False):
        self.host = _host or self
        if _host is None:
            self.workspace_lock = threading.RLock()
            self.folder_picker_lock = threading.Lock()
            self.directories = GameDirectories(home, game)
            self.onboarding = Onboarding(self.directories)
            self.language = Language(self.directories.data)
            self.workspace_epoch = secrets.token_hex(16)
            self.active = self
        else:
            self.workspace_lock = _host.workspace_lock
            self.directories = _host.directories
            self.language = _host.language
        self.workspace_id = None if _empty else (_ident or self.directories.value['current'])
        data = self.directories.location(self.workspace_id) if self.workspace_id else self.directories.data / 'unselected'
        entry = (_entry or self.directories.entry(self.workspace_id)) if self.workspace_id else {'path': str(data / 'game')}
        self.c = Catalog(entry['path'], home, data=data,
                         create_active=bool(self.workspace_id and _host is None and self.directories.created),
                         disabled_folder=entry.get('disabled_folder', library_paths.DEFAULT_VAULT))
        self.ops = Operations(self.c)
        self.ops.ensure_default_profile()
        self.ai = AI(self.c, language=self.language)
        self.downloads = Downloads(self.c)
        self.importer = Importer(self.c,self.ai,self.downloads,self.ops)
        self.name_repairs = NameRepairs(self.c,self.ops,self.ai)
        self.package_repairs = PackageRepairs(self.c,self.ops,self.ai)
        self.jobs = Jobs()
        self.summaries = SummaryQueue(self.c, self.ai, self.jobs)
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.agent = Agent(self)
        self.covers = Covers(self.c)
        self.closing = _host.closing if _host else threading.Event()
        self.close_lock = threading.Lock()
        self.close_worker = None
        self.pages = _host.pages if _host else PageSessions(self.request_shutdown)
        self.appearance = Appearance(self.c.data)
        self.library_preferences = LibraryPreferences(self.c.data)
        self.chat_presets = ChatPresets(self.c.data, language=self.language)
        self.home_layout = HomeLayout(self.c.data)
        self.quarantine = Quarantine(self.c, self.ops)

    def request_shutdown(self):
        host = self.host
        with host.workspace_lock, host.close_lock:
            if host.closing.is_set(): return {'closed': True}
            current = host.active
            host.closing.set()
            host.pages.stop()
            current.summaries.suspend()
            current.jobs.stop()
            def finish():
                try:
                    current.agent.close()
                    current.ai.close()
                finally:
                    current.jobs.wait()
                    if host.server: host.server.shutdown()
            host.close_worker = threading.Thread(target=finish, daemon=True)
            host.close_worker.start()
        return {'closed': True}

    def workspace_info(self):
        host = self.host
        if not self.workspace_id:
            return {'id': None, 'name': '未选择游戏目录', 'path': '', 'epoch': host.workspace_epoch, 'available': False}
        item = host.directories.entry(self.workspace_id)
        return {**item, **library_paths.public_paths(self.c.game, self.c.data),
                'storage': self.c.storage.public(),
                'epoch': host.workspace_epoch, 'available': host.directories.available(item)}

    def check_workspace(self, scope, write=False):
        host = self.host
        if scope is None: return  # Trusted in-process calls (including agent tools).
        if ((scope and scope != host.workspace_epoch)
                or (write and not scope and len(host.directories.value['items']) > 1)):
            raise WorkspaceChanged('游戏目录已切换，请刷新页面后再操作。')

    def switch_workspace(self, ident):
        host = self.host
        if not ident: raise ValueError('请选择已保存的游戏目录。')
        if ident == host.active.workspace_id: return host.active.workspace_info()
        target = host.directories.entry(ident)
        current = host.active
        if host.closing.is_set(): raise ValueError('包管家正在退出，请重新打开。')
        if current.jobs.busy() or current.agent.busy():
            raise ValueError('当前目录有任务正在运行，请等待完成，或暂停生成／停止对话后再切换。')
        current.ops.check_pending()
        if not host.directories.available(target):
            raise ValueError('此游戏目录当前不可用，请检查路径或磁盘连接；已有数据保留。')
        destination = host.directories.location(ident)
        # New directories inherit connection/appearance preferences once, never resource data.
        if not destination.exists() or current.workspace_id is None:
            destination.mkdir(parents=True, exist_ok=True)
            for name in ('ai-settings.json', 'appearance.json'):
                source = current.c.data / name
                if source.is_file() and not (destination / name).exists():
                    shutil.copy2(source, destination / name)
        candidate = Application(target['path'], current.c.home, _host=host, _ident=ident)
        try:
            host.directories.activate(ident)
        except Exception:
            candidate.agent.close(); candidate.ai.close()
            raise
        host.active = candidate
        host.workspace_epoch = secrets.token_hex(16)
        current.agent.close(); current.ai.close()
        return candidate.workspace_info()

    def get(self, path, query, scope=None):
        host = self.host
        with host.workspace_lock:
            if path != '/api/status': host.check_workspace(scope)
            current = host.active
            if path in ('/api/status', '/api/game-directories'):
                return current._get(path, query)
        # Keep the selected session immutable while slow reads complete.
        return current._get(path, query)

    def change_library_folder(self, data):
        host, current = self.host, self.host.active
        if data.get('active_folder', 'AddonPackages') != 'AddonPackages':
            raise ValueError('启用文件夹固定为 AddonPackages，不能修改。')
        name = library_paths.folder_name(data.get('disabled_folder'))
        if host.closing.is_set(): raise ValueError('包管家正在退出，请重新打开。')
        if current.jobs.busy() or current.agent.busy():
            raise ValueError('当前目录有任务正在运行，请等待完成或暂停任务后再修改文件夹。')
        current.ops.check_pending()
        if not current.workspace_id or not host.directories.available(host.directories.entry(current.workspace_id)):
            raise ValueError('当前游戏目录不可用；已有数据保留。')
        host.directories.check_location(current.c.game, current.workspace_id, name)
        # Build a separate session so late reads retain their captured library paths.
        candidate = Application(current.c.game, current.c.home, _host=host, _ident=current.workspace_id)
        try:
            library_paths.change(candidate.c, name)
        except Exception:
            candidate.agent.close(); candidate.ai.close()
            raise
        host.active = candidate
        host.workspace_epoch = secrets.token_hex(16)
        current.agent.close(); current.ai.close()
        return candidate.workspace_info()

    def edit_directory_binding(self, ident, path=None, *, remove=False, name=None):
        host, current = self.host, self.host.active
        if not host.directories.valid_id(ident): raise ValueError('请选择要修改绑定的游戏目录。')
        host.directories.entry(ident)
        if host.closing.is_set(): raise ValueError('包管家正在退出，请重新打开。')
        if current.jobs.busy() or current.agent.busy():
            raise ValueError('当前有任务正在运行，请等待完成或暂停后再修改目录绑定。')
        if (host.directories.location(ident) / 'library-path-change.json').exists():
            raise ValueError('此目录有待恢复的停用库路径变更，请先恢复后再修改绑定。')
        if (host.directories.location(ident) / 'storage-migration.json').exists():
            raise ValueError('此目录有待继续的管理文件迁移，请先完成迁移后再修改绑定。')
        item = None if remove else host.directories.prepare_binding(ident, path, name)
        candidate = None
        if ident == current.workspace_id:
            candidate = Application(current.c.game, current.c.home, _host=host, _empty=remove,
                                    _ident=None if remove else ident, _entry=item)
        try:
            if remove: host.directories.archive(ident)
            else: host.directories.save_binding(item)
        except Exception:
            if candidate: candidate.agent.close(); candidate.ai.close()
            raise
        if candidate:
            host.active = candidate
            host.workspace_epoch = secrets.token_hex(16)
            current.agent.close(); current.ai.close()
        return {'directories': host.directories.public(), 'workspace': host.active.workspace_info()}

    def post(self, path, data, from_agent=False, scope=None):
        host = self.host
        if path == '/api/game-directories/browse':
            # Native dialogs may stay open for minutes; polling/switch/shutdown must
            # not wait on the workspace lock while the user is choosing a folder.
            with host.workspace_lock:
                host.check_workspace(scope, write=True)
                if host.closing.is_set(): raise ValueError('包管家正在退出，请重新打开。')
                epoch = host.workspace_epoch
                initial = data.get('path') or (str(host.active.c.game) if host.active.workspace_id else '')
                if not isinstance(initial, str): raise ValueError('目录路径无效。')
            if not host.folder_picker_lock.acquire(blocking=False):
                raise ValueError('文件夹选择窗口已打开，请先选择或取消。')
            try:
                selected = choose_folder(initial, language='en') if host.language.get()['language']=='en' else choose_folder(initial)
                with host.workspace_lock:
                    host.check_workspace(epoch)
                    if host.closing.is_set(): raise ValueError('包管家正在退出，请重新打开。')
                    if not selected: return {'cancelled': True}
                    selected = host.directories.game_path(selected)
                    return {'path': str(selected), 'name': selected.name, 'cancelled': False}
            finally:
                host.folder_picker_lock.release()
        with host.workspace_lock:
            if not path.startswith('/api/ui-session/'):
                host.check_workspace(scope, write=True)
                if path != '/api/shutdown' and (host.active.c.data / 'library-path-change.json').exists():
                    host.active.ops.check_pending()
                if path not in ('/api/shutdown', '/api/cancel', '/api/game-storage/migrate', '/api/agent/stop'):
                    host.active.c.storage.check_pending()
            if path == '/api/onboarding':
                action = data.get('action')
                if action == 'finish': return host.onboarding.set('done')
                if action == 'skip_game': return host.onboarding.set('ai')
                if action == 'select_game':
                    game = host.directories.game_path(data.get('path'))
                    item = next((p for p in host.directories.value['items']
                                 if not p.get('archived') and host.directories.path_key(p['path']) ==
                                 host.directories.path_key(game)), None)
                    if item is None:
                        host.directories.register(str(game), data.get('name', ''))
                        item = next(p for p in host.directories.value['items']
                                    if not p.get('archived') and host.directories.path_key(p['path']) ==
                                    host.directories.path_key(game))
                    host.switch_workspace(item['id'])
                    return host.onboarding.set('ai')
                raise ValueError('首次设置操作无效。')
            if path == '/api/game-directories/add':
                if data.get('active_folder', 'AddonPackages') != 'AddonPackages':
                    raise ValueError('启用文件夹固定为 AddonPackages，不能修改。')
                return host.directories.register(data.get('path'), data.get('name', ''),
                                                  data.get('disabled_folder', library_paths.DEFAULT_VAULT))
            if path == '/api/game-directories/rename':
                return host.directories.rename(data.get('id'), data.get('name'))
            if path == '/api/game-directories/remove':
                return host.edit_directory_binding(data.get('id'), remove=True)
            if path == '/api/game-directories/rebind':
                return host.edit_directory_binding(data.get('id'), data.get('path'), name=data.get('name'))
            if path == '/api/game-directories/switch':
                return host.switch_workspace(data.get('id'))
            if path == '/api/game-directories/library-folders':
                return host.change_library_folder(data)
            return host.active._post(path, data, from_agent)

    def _get(self, path, query):
        if self.closing.is_set() and path != '/api/status':
            raise ValueError('包管家正在退出，请重新双击 EXE 打开。')
        if path == '/api/game-directories': return self.directories.public()
        if path == '/api/onboarding': return self.host.onboarding.get()
        if path == '/api/home-layout': return self.home_layout.get()
        if path == '/api/status':
            stats = self.c.stats()
            if not self.workspace_id: stats = {**stats, 'game': '', 'disabled_folder': ''}
            return {'workspace': self.workspace_info(), 'game_directories': self.directories.public(),
                    'home_layout': self.home_layout.get(), 'onboarding':self.host.onboarding.get(),
                    'version':__version__, 'busy':self.jobs.busy(),'stats':stats,
                    'appearance':self.appearance.get(), 'language':self.language.get(),
                    'library_preferences':self.library_preferences.get(),
                    'revision':self.c.revision,
                    'agent_busy':self.agent.busy(),
                    'summary_queue':self.summaries.public(),
                    'ai_usage':self.ai.usage.public(),
                    'jobs':self.jobs.all(), 'pending':any(h['status'] in ('running','interrupted','undoing') for h in self.ops.history())}
        if path == '/api/agent/tools': return {'tools':TOOLS}
        if path == '/api/agent/state': return self.agent.snapshot(int(query.get('after',0)),query.get('chat_id'))
        if path == '/api/job':
            job = self.jobs.jobs.get(query.get('id',''))
            if not job: raise ValueError('任务记录已过期')
            return job.public()
        if path == '/api/packages': return self.c.list(query)
        if path == '/api/package-keys': return self.c.list(dict(query,keys_only=True))
        if path == '/api/package': return self.c.detail(query.get('key',''))
        if path == '/api/folders':
            rows=self.c.folders();q=query.get('q','').casefold()
            if q:rows=[r for r in rows if q in r['path'].casefold()]
            limit=min(3000,max(1,int(query.get('limit',3000))))
            return {'items':rows[:limit],'total':len(rows),'favorite_folder':self.c.favorite_folder()}
        if path == '/api/missing':
            rows=self.c.missing()
            q=query.get('q','').casefold()
            if q: rows=[r for r in rows if q in (r['ref']+' '+' '.join(r['users'])).casefold()]
            page=min(max(1,(len(rows)+79)//80),max(1,int(query.get('page',1))))
            return {'total':len(rows),'items':rows[(page-1)*80:page*80],'page':page}
        if path == '/api/duplicates':
            audit=read_json(self.c.data/'duplicate-audit.json',{'created':None,'groups':[],'conflicts':[],'extras':0,'bytes':0})
            audit['stale']=bool(audit['created'] and audit.get('signature')!=self.c.signature())
            return audit
        if path == '/api/quarantine': return self.quarantine.listing(query.get('batch'), query.get('page', 1))
        if path == '/api/sources': return self.downloads.get()
        if path == '/api/package-repairs': return self.package_repairs.status()
        if path == '/api/package-repairs/preview': return self.package_repairs.last_preview()
        if path == '/api/name-repairs': return self.name_repairs.status()
        if path == '/api/name-repairs/preview': return self.name_repairs.last_preview()
        if path == '/api/source-settings': return self.downloads.sources.settings()
        if path == '/api/download-history':
            files=sorted((self.c.data/'download-history').glob('*.json'),reverse=True)[:20]
            return [read_json(p) for p in files]
        if path == '/api/profiles': return self.ops.profiles()
        if path == '/api/import-options':
            return dict(self.ops.import_target(),extensions=['.var']+sorted(ASSET_EXTENSIONS),max_files=MAX_IMPORT_FILES)
        if path == '/api/history': return self.ops.history()
        if path == '/api/settings': return self.ai.settings()
        if path == '/api/language': return self.language.get()
        if path == '/api/appearance': return self.appearance.get()
        if path == '/api/library-preferences': return self.library_preferences.get()
        if path == '/api/chat-presets': return self.chat_presets.get()
        if path == '/api/codex-status': return self.ai.status()
        if path in ('/api/codex-models','/api/ai-models'):
            provider='codex' if path=='/api/codex-models' else query.get('provider')
            return self.ai.models.get(provider,query.get('refresh')=='1',self.closing,service=query.get('service'))
        raise ValueError('接口不存在')

    def _post(self,path,data,from_agent=False):
        if path == '/api/shutdown': return self.request_shutdown()
        if path == '/api/ui-session/close':
            self.pages.close(data.get('id'))
            return {'closed':True}
        if self.closing.is_set(): raise ValueError('包管家正在退出，请重新双击 EXE 打开。')
        if path == '/api/ui-session/launch':
            self.pages.launching()
            return {'ready':True}
        local_reveal = path == '/api/reveal' and not data.get('key')
        if not self.workspace_id and not local_reveal and path not in ('/api/language', '/api/home-layout', '/api/appearance', '/api/library-preferences',
                '/api/chat-presets', '/api/settings', '/api/codex-login', '/api/source-settings'):
            raise ValueError('请先在设置中添加、恢复或选择游戏目录。')
        if path == '/api/home-layout': return self.home_layout.save(data)
        if path == '/api/language': return self.language.save(data)
        if path == '/api/appearance': return self.appearance.save(data)
        if path == '/api/library-preferences': return self.library_preferences.save(data)
        if path == '/api/chat-presets': return self.chat_presets.save(data)
        def start(title,callback):
            return self.jobs.start(title,callback,origin='agent' if from_agent else 'ui')
        if path == '/api/agent/send':
            return self.agent.send(data.get('message'),bool(data.get('read_only')),data.get('selected_keys'))
        if path == '/api/agent/stop': return self.agent.stop()
        if path == '/api/agent/reset': return self.agent.reset()
        if path == '/api/cancel':
            job=self.jobs.jobs.get(data.get('id'))
            if job: job.cancel.set()
            return {'cancelled':bool(job)}
        if path == '/api/ai-queue/pause': return self.summaries.pause()
        if path == '/api/ai-queue/dismiss': return self.summaries.dismiss(data.get('run_id'))
        if self.agent.busy() and not from_agent:
            raise ValueError('对话助手正在处理任务，请等待完成或在对话页停止任务后再操作。')
        if path == '/api/agent/tool': return invoke(self,data.get('name'),data.get('arguments',{}))
        if self.jobs.busy(): raise ValueError('有任务正在执行，请等待完成或取消后再操作。')
        if not local_reveal and path not in ('/api/settings', '/api/codex-login', '/api/source-settings') and not self.directories.available(self.directories.entry(self.workspace_id)):
            raise ValueError('当前游戏目录不可用，请在设置中切换目录；已有数据保留。')
        if path == '/api/game-storage/migrate':
            self.ops.check_pending(allow_storage=True)
            def migrate(job):
                try: return self.c.storage.migrate(job)
                finally:
                    self.ops.plans.clear()
                    self.quarantine.plans.clear()
            return start('迁移游戏管理文件', migrate)
        if path == '/api/chat-presets/draft':
            title=text_field(data.get('title'),'标题',60)
            prompt=text_field(data.get('prompt',''),'原提示词',12000,required=False)
            return self.jobs.start('生成预设提示词',lambda j:self.ai.draft_prompt(title,prompt,j),origin='preset')
        if path == '/api/ai-queue/start':
            return self.summaries.start(data.get('scope'), data.get('keys'), origin='agent' if from_agent else 'ui',mode=data.get('mode','all'),target=data.get('target','summary'))
        if path == '/api/ai-queue/resume': return self.summaries.resume(origin='agent' if from_agent else 'ui')
        if path == '/api/ai-queue/finish': return self.summaries.finish()
        if path == '/api/source-settings': return self.downloads.sources.save(data)
        if path == '/api/scan': return start('深度扫描' if data.get('deep') else '增量扫描',lambda j:self.scan(j,bool(data.get('deep'))))
        if path == '/api/import':
            self.ops.check_pending()
            run=self.importer.run_session if data.get('session') else self.importer.run
            return start('导入资源',lambda j:self.sync_library(
                run(data.get('session') or data.get('ids',[]),j,bool(data.get('ai')),bool(data.get('find'))),j))
        if path == '/api/import-session':
            self.ops.check_pending()
            return self.importer.create_session()
        if path == '/api/import-session/append':
            self.ops.check_pending()
            return self.importer.append_session(data.get('session'),data.get('ids'))
        if path == '/api/import-session/discard':return self.importer.discard_session(data.get('session'))
        if path == '/api/discard-import':return self.importer.discard(data.get('ids',[]))
        if path == '/api/audit': return start('重复包内容校验',self.c.duplicate_audit)
        if path == '/api/package-repairs/analyze': return start('AI 分析包异常',lambda j:self.package_repairs.analyze(j,bool(data.get('refresh'))))
        if path == '/api/package-repairs/apply': return start('修复包异常',lambda j:self.package_repairs.apply(data.get('id'),data.get('items'),j))
        if path == '/api/name-repairs/analyze': return start('AI 分析异常包名',lambda j:self.name_repairs.analyze(j,bool(data.get('refresh'))))
        if path == '/api/name-repairs/apply': return start('修复包文件名',lambda j:self.name_repairs.apply(data.get('id'),data.get('items'),j))
        if path == '/api/plan': return start('生成操作预览',lambda j:self.ops.plan(
            data.get('kind'),data.get('keys',[]),j,data.get('folder'),data.get('profile_id'),
            duplicate_mode=data.get('duplicate_mode'),copy_choices=data.get('copy_choices'),
            remember_copies=data.get('remember_copies',False),archive_scope=data.get('scope')))
        if path == '/api/apply': return start('执行文件操作',lambda j:self.ops.apply(data.get('id'),j))
        if path == '/api/undo': return start('恢复文件操作',lambda j:self.ops.undo(data.get('id'),j))
        if path == '/api/quarantine/preview':
            return start('预览隔离区清理',lambda j:self.quarantine.preview(data,j))
        if path == '/api/quarantine/purge':
            return start('永久清理隔离区',lambda j:self.quarantine.purge(data.get('id'),data.get('confirmed'),j))
        if path == '/api/find-dependencies': return start('查找依赖下载来源',lambda j:self.downloads.find(j,data.get('keys')))
        if path == '/api/install-dependencies':
            self.ops.check_pending()
            return start('下载并校验依赖',lambda j:self.sync_library(self.downloads.install(j,data.get('names')),j))
        if path == '/api/ai': return start('AI 中文分类与简介',lambda j:self.ai.generate(data.get('keys',[]),j,bool(data.get('overwrite')),bool(data.get('refresh_ai'))))
        if path == '/api/label':
            if data.get('favorite') is not None:
                def label_and_move(job):
                    out=self.ops.favorite([data.get('key','')],bool(data['favorite']),job)
                    if data.get('category') is not None or data.get('summary') is not None or data.get('tags') is not None:
                        self.c.label(data['key'],category=data.get('category'),summary=data.get('summary'),tags=data.get('tags'))
                    return out
                return start('收藏并归档' if data['favorite'] else '取消收藏并移回',label_and_move)
            self.c.label(data.get('key',''),category=data.get('category'),summary=data.get('summary'),favorite=data.get('favorite'),tags=data.get('tags'))
            return {'saved':True}
        if path == '/api/favorite':return start('收藏并归档' if data.get('favorite') else '取消收藏并移回',
            lambda j:self.ops.favorite(data.get('keys',[]),bool(data.get('favorite')),j))
        if path == '/api/adopt-favorites':return start('设为收藏文件夹',
            lambda j:self.ops.adopt_favorites(data.get('folder'),j,data.get('profile_name')))
        if path == '/api/rename-favorite-folder':return start('重命名收藏目录',
            lambda j:self.ops.rename_collection(data.get('folder'),j))
        if path == '/api/profile': return self.ops.save_profile(data.get('name',''),data.get('keys',[]),data.get('id'),data.get('folder'),bool(data.get('favorites')))
        if path == '/api/delete-profile': return self.ops.delete_profile(data.get('id'))
        if path == '/api/settings': return self.ai.save(data)
        if path == '/api/codex-login': return self.ai.login()
        if path == '/api/launch': return self.ops.launch()
        if path == '/api/reveal':
            key=str(data.get('key','')).casefold()
            if key:
                p=self.c.packages().get(key)
                if not p:raise ValueError('包已不存在，请更新索引。')
                file_id=data.get('file_id')
                row=next((r for r in p['files'] if r['id']==file_id),None) if file_id is not None else p['files'][0]
                if not row:raise ValueError('所选副本不存在。')
                return reveal_file(self.c.path(row))
            if os.name!='nt':return {'opened':False}
            os.startfile(str(self.c.home))
            return {'opened':True,'selected':False}
        raise ValueError('接口不存在')

    def thumbnail(self,key):
        return self.covers.get(key)

    def scan(self,job,deep=False):
        result=self.c.scan(job,deep)
        return self.sync_library(result,job)

    def sync_library(self,result,job):
        result['activation_sync']=self.ops.sync_activation(job)
        return result


def serve(app,port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def respond(self,data,status=200,mime='application/json; charset=utf-8',cache='no-store'):
            if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control',cache)
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError): pass

        def allowed(self,api=False):
            host=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host')!=host: return False
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+host: return False
            if self.headers.get('Sec-Fetch-Site')=='cross-site': return False
            if api and not hmac.compare_digest(self.headers.get('X-Session-Token',''),app.token): return False
            return True

        def do_GET(self):
            u=urllib.parse.urlsplit(self.path)
            api=u.path.startswith('/api/')
            if not self.allowed(api): return self.respond({'error':'请通过 EXE 启动并打开本机管理页面。'},403)
            try:
                q={k:v[0] for k,v in urllib.parse.parse_qs(u.query).items()}
                if u.path == '/api/ui-session': return self.page_session(q.get('id'))
                if u.path=='/api/thumbnail':
                    with app.workspace_lock:
                        app.check_workspace(self.headers.get('X-Workspace-Epoch', q.get('workspace', '')))
                        current=app.active
                    result=current.thumbnail(q.get('key',''))
                    if not result: return self.respond(b'',status=204)
                    b,mime=result
                    return self.respond(b,mime=mime,cache='private, max-age=86400')
                if api: return self.respond(app.get(u.path,q,scope=self.headers.get('X-Workspace-Epoch','')))
                if u.path == '/appearance-config.js':
                    # Only validated cosmetic enums are public; no paths or credentials.
                    config = 'window.VaMPMAppearanceConfig=' + json.dumps(app.active.appearance.get()) + ';'
                    return self.respond(config.encode(), mime='text/javascript; charset=utf-8')
                if u.path == '/language-config.js':
                    config = 'window.VaMPMLanguageConfig=' + json.dumps(app.language.get()) + ';'
                    return self.respond(config.encode(), mime='text/javascript; charset=utf-8')
                files={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg',
                       '/appearance.js':'appearance.js','/themes.css':'themes.css',
                       '/i18n.js':'i18n.js','/locales/en.js':'locales/en.js',
                       '/home.js':'home.js','/home.css':'home.css','/workspaces.js':'workspaces.js','/onboarding.js':'onboarding.js','/quarantine.js':'quarantine.js',
                       '/pagination.js':'pagination.js','/pagination.css':'pagination.css','/models.js':'models.js','/cards.js':'cards.js',
                       '/activation.js':'activation.js','/imports.js':'imports.js','/selection.js':'selection.js','/selection.css':'selection.css',
                       '/chat-presets.js':'chat-presets.js','/chat-presets.css':'chat-presets.css','/chat-composer.js':'chat-composer.js',
                       '/covers.js':'covers.js','/lifecycle.js':'lifecycle.js','/vendor/marked.js':'vendor/marked.js','/vendor/purify.js':'vendor/purify.js'}
                name=files.get(u.path)
                if not name: return self.respond({'error':'未找到'},404)
                mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.svg':'image/svg+xml'}[Path(name).suffix]
                self.respond((app.c.home/'web'/name).read_bytes(),mime=mime)
            except WorkspaceChanged as e: self.respond({'error':str(e),'workspace_changed':True},409)
            except Exception as e: self.respond({'error':str(e)},400)

        def page_session(self, ident):
            ticket=app.pages.open(ident)
            try:
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Cache-Control','no-store')
                self.send_header('Connection','close')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                self.connection.settimeout(5)
                while not ticket.is_set():
                    self.wfile.write(b': page-connected\n\n')
                    self.wfile.flush()
                    if ticket.wait(1): break
            except (OSError,ValueError): pass
            finally:
                app.pages.close(ident,ticket)
                self.close_connection=True

        def do_POST(self):
            if not self.allowed(True): return self.respond({'error':'请求被拒绝，请重新通过 EXE 打开。'},403)
            try:
                if urllib.parse.urlsplit(self.path).path=='/api/upload':
                    if app.closing.is_set(): raise ValueError('包管家正在退出，请重新打开后操作。')
                    with app.workspace_lock:
                        app.check_workspace(self.headers.get('X-Workspace-Epoch',''),write=True)
                        current=app.active
                        if not current.workspace_id or not current.directories.available(current.directories.entry(current.workspace_id)):
                            raise ValueError('当前游戏目录不可用，请在设置中切换目录；已有数据保留。')
                        if not current.jobs.lock.acquire(blocking=False): raise ValueError('请等待当前任务完成后再导入。')
                    try:
                        current.ops.check_pending()
                        self.connection.settimeout(120)
                        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                        result=current.importer.upload(self.rfile,q.get('name',[''])[0],int(self.headers.get('Content-Length','0')),
                                                   q.get('relative_path',[None])[0])
                    finally:
                        current.jobs.lock.release()
                    return self.respond(result)
                if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise ValueError('需要 JSON 请求')
                n=int(self.headers.get('Content-Length','0'))
                if n<0 or n>1024*1024: raise ValueError('请求过大')
                data=json.loads(self.rfile.read(n))
                if not isinstance(data,dict): raise ValueError('请求格式无效')
                self.respond(app.post(urllib.parse.urlsplit(self.path).path,data,scope=self.headers.get('X-Workspace-Epoch','')))
            except WorkspaceChanged as e: self.respond({'error':str(e),'workspace_changed':True},409)
            except Exception as e: self.respond({'error':str(e)},400)

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    app.server=server
    return server
