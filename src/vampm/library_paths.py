"""Per-library folder names and recoverable, non-overwriting vault renames."""
import os
import re
import sqlite3

from .operations import move_no_replace
from .file_moves import recover_move
from .util import atomic_json, read_json, safe_path, require_game_closed

DEFAULT_VAULT = '.DisabledPackages'


def folder_name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 80
            or value != value.strip() or value.endswith('.')
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
            or value in ('.', '..')
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', value, re.I)):
        raise ValueError('停用文件夹名需为 1–80 个字符，不能含路径分隔符、非法字符或系统保留名称。')
    if value.casefold() in {'addonpackages', 'saves', 'custom', 'vam_data', 'data', 'vampackagemanager',
                           '.packagemanagerstorage'}:
        raise ValueError('不能将启用库、游戏资源目录或管家数据目录作为停用库。')
    return value


def configuration(data, default=DEFAULT_VAULT):
    """Read without creating a database when a workspace has not been opened yet."""
    values = {}
    dbpath = data / 'catalog.sqlite3'
    if dbpath.exists():
        db = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True, timeout=30)
        try:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone():
                values = dict(db.execute("SELECT key,value FROM settings WHERE key IN ('disabled_location','disabled_folder')"))
        finally:
            db.close()
    mode = values.get('disabled_location', 'legacy' if (data / 'disabled').exists() else 'game')
    if mode not in ('game', 'legacy'): raise ValueError('停用库位置配置无效')
    return {'mode': mode, 'folder': folder_name(values.get('disabled_folder', default))}


def vault_path(game, data, config):
    return safe_path(game, config['folder']) if config['mode'] == 'game' else safe_path(data, 'disabled')


def public_paths(game, data, default=DEFAULT_VAULT):
    config = configuration(data, default)
    return {'active_folder': 'AddonPackages', 'disabled_folder': config['folder'],
            'disabled_location': config['mode'], 'disabled_path': str(vault_path(game, data, config))}


def save_configuration(c, config):
    with c.db() as db:
        db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       [('disabled_location', config['mode']), ('disabled_folder', config['folder'])])


def recover(c):
    """A crash before the DB commit rolls back the directory rename on next open."""
    journal = c.data / 'library-path-change.json'
    if not journal.exists(): return
    record = read_json(journal)
    if (not isinstance(record, dict) or record.get('version') != 1
            or type(record.get('had_source')) is not bool):
        raise ValueError('停用库路径恢复记录无效，已有资源保留。')
    old, new = record.get('old', {}), record.get('new', {})
    for config in (old, new):
        if not isinstance(config, dict) or config.get('mode') not in ('game', 'legacy'):
            raise ValueError('停用库路径恢复记录无效，已有资源保留。')
        folder_name(config.get('folder'))
    if new['mode'] != 'game' or old == new:
        raise ValueError('停用库路径恢复记录无效，已有资源保留。')
    source, destination = vault_path(c.game, c.data, old), vault_path(c.game, c.data, new)
    current = configuration(c.data)
    if current not in (old, new): raise ValueError('停用库路径配置与恢复记录不一致，未改动资源。')
    if record['had_source']:
        recover_move(source, destination, finish=True)
        recover_move(destination, source, finish=True)
        expected, other = (destination, source) if current == new else (source, destination)
        if other.exists():
            if current == new or expected.exists() or not other.is_dir():
                raise ValueError('停用库路径恢复遇到冲突，未覆盖任何文件。')
            require_game_closed()
            move_no_replace(other, expected)
        if not expected.is_dir(): raise ValueError('停用库暂时不可用，请连接原磁盘后重试；恢复记录保留。')
    journal.unlink()


def change(c, name):
    name = folder_name(name)
    old = configuration(c.data)
    new = {'mode': 'game', 'folder': name}
    if old == new: return
    recover(c)
    source, destination = vault_path(c.game, c.data, old), vault_path(c.game, c.data, new)
    if os.path.normcase(str(source)) == os.path.normcase(str(destination)):
        raise ValueError('请使用不同的文件夹名称；仅改变大小写不受支持。')
    if destination.exists(): raise ValueError('目标文件夹已存在，请换一个名称；不会合并或覆盖已有内容。')
    if source.exists() and not source.is_dir(): raise ValueError('原停用库不是文件夹，未改动资源。')
    require_game_closed()
    journal = c.data / 'library-path-change.json'
    atomic_json(journal, {'version': 1, 'old': old, 'new': new, 'had_source': source.exists()})
    try:
        if source.exists(): move_no_replace(source, destination)
        save_configuration(c, new)
    except Exception:
        recover(c)
        raise
    # Once committed, a leftover journal is safe to finish on the next startup.
    try: journal.unlink()
    except OSError: pass
    c.roots['vault'] = destination
    c.invalidate()
