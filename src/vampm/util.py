import ctypes
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

CATEGORIES = ["场景", "人物", "服饰", "头发", "皮肤纹理", "变形", "插件", "环境道具", "姿势动作", "其他"]
PACKAGE = re.compile(r"^([^./\\:]+)\.([^/\\:]+)\.(\d+|latest|min\d+)$", re.I)


def split_id(value):
    value = str(value)
    if value.lower().endswith('.var'):
        value = value[:-4]
    m = PACKAGE.fullmatch(value)
    if not m:
        return None
    a, n, v = m.groups()
    return a, n, v.lower()


def matches(ref, actual):
    r, a = split_id(ref), split_id(actual)
    return bool(r and a and r[:2][0].casefold() == a[0].casefold()
                and r[1].casefold() == a[1].casefold() and a[2].isdigit()
                and (r[2] == 'latest' or r[2] == a[2]
                     or (r[2].startswith('min') and int(a[2]) >= int(r[2][3:]))))


def stamp():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text('utf-8-sig'))
    except FileNotFoundError:
        return default


def safe_path(root, rel):
    root = Path(root).resolve()
    if not isinstance(rel, str) or not rel or '\\' in rel or ':' in rel:
        raise ValueError('无效的相对路径')
    parts = rel.split('/')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts):
        raise ValueError('路径不能包含上级目录或空白目录名')
    p = root
    for part in parts:
        p = p / part
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('不操作符号链接或目录联接：' + rel)
    if not p.resolve().is_relative_to(root):
        raise ValueError('路径超出管理目录')
    return p


def fingerprint(path):
    s = Path(path).stat()
    return [s.st_size, s.st_mtime_ns]


def sha256(path, job=None):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while b := f.read(4 * 1024 * 1024):
            if job:
                job.check()
            h.update(b)
    return h.hexdigest()


def hidden_process_args():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def stop_process(process):
    """Stop only an owned subprocess (and its Windows child tree)."""
    if process.poll() is not None: return
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],
                           capture_output=True, timeout=8, **hidden_process_args())
        except (OSError,subprocess.TimeoutExpired): pass
    else:
        try: process.terminate()
        except OSError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill();process.wait(timeout=5)


def cancellable_read(callback, cancel):
    """Interrupt waiting for read-only HTTP inference without waiting for its timeout.

    The detached daemon may only fetch response bytes, never modify files or labels;
    process exit closes its socket if the peer has stopped responding.
    """
    replies = queue.Queue(maxsize=1)
    def run():
        try: replies.put((True,callback()))
        except Exception as e: replies.put((False,e))
    if cancel.is_set(): raise Cancelled('任务已停止；已保存的结果保留。')
    threading.Thread(target=run,daemon=True).start()
    while True:
        if cancel.is_set(): raise Cancelled('任务已停止；已保存的结果保留。')
        try: ok,value=replies.get(timeout=.1)
        except queue.Empty: continue
        if not ok: raise value
        return value


def network_process_env():
    """Respect Windows' configured proxy in child CLIs that only inspect env vars."""
    import urllib.request
    env=os.environ.copy()
    configured=urllib.request.getproxies()
    for scheme in ('http','https'):
        if configured.get(scheme) and not any(k.lower()==scheme+'_proxy' for k in env):
            env[scheme.upper()+'_PROXY']=configured[scheme]
    return env


def vam_running():
    if os.name != 'nt':
        # Linux development runs are fixtures only; the Windows build checks tasklist.
        return False
    result = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq VaM.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, timeout=10, **hidden_process_args())
    if result.returncode:
        raise RuntimeError('无法检查 VaM 运行状态，请稍后重试')
    return b'"vam.exe"' in result.stdout.lower()


def require_game_closed():
    if vam_running():
        raise ValueError('请先退出 VaM，再移动、清理、补包或切换启用方案。')


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, title):
        self.id = uuid.uuid4().hex
        self.title = title
        self.status = 'running'
        self.current = 0
        self.total = 0
        self.message = ''
        self.created = stamp()
        self.result = None
        self.error = None
        self.cancel = threading.Event()

    def update(self, current, total, message=''):
        self.current, self.total, self.message = current, total, message

    def check(self):
        if self.cancel.is_set():
            raise Cancelled('任务已取消；已完成的步骤保留在记录中。')

    def public(self, result=True):
        return {k: v for k, v in vars(self).items() if k != 'cancel' and (result or k != 'result')}


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.stopping = threading.Event()

    def start(self, title, callback, origin='ui'):
        if self.stopping.is_set(): raise ValueError('包管家正在退出，请重新打开后操作。')
        if not self.lock.acquire(blocking=False):
            raise ValueError('已有任务正在运行，请等待完成或取消。')
        job = Job(title)
        job.origin = origin
        # Completed results are fetched once, not retransmitted on every progress poll.
        while len(self.jobs) >= 40:
            self.jobs.pop(next(iter(self.jobs)))
        self.jobs[job.id] = job
        if self.stopping.is_set(): job.cancel.set()
        def run():
            try:
                job.check()
                job.result = callback(job)
                job.status = 'done'
            except Cancelled as e:
                job.error, job.status = str(e), 'cancelled'
            except Exception as e:
                job.error, job.status = str(e), 'error'
            finally:
                self.lock.release()
        threading.Thread(target=run, daemon=True).start()
        return job.public()

    def busy(self):
        return self.lock.locked()

    def all(self):
        return [j.public(result=False) for j in list(self.jobs.values())[-20:]][::-1]

    def stop(self):
        self.stopping.set()
        for job in list(self.jobs.values()):
            if job.status == 'running': job.cancel.set()

    def wait(self):
        # Includes uploads and file-operation recovery records; never exit mid-move.
        with self.lock: pass


def protect_secret(text):
    if not text:
        return ''
    if os.name != 'nt':
        raise ValueError('API 密钥仅在 Windows 版中保存（Windows 用户加密）。开发模式请用环境变量 VAMPM_API_KEY。')
    import base64
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    b = text.encode('utf-8')
    buf = ctypes.create_string_buffer(b)
    source, target = Blob(len(b), buf), Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('Windows 密钥加密失败')
    try:
        return base64.b64encode(ctypes.string_at(target.pbData, target.cbData)).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def unprotect_secret(text):
    if not text:
        return os.environ.get('VAMPM_API_KEY', '')
    if os.name != 'nt':
        raise ValueError('此密钥需要在原 Windows 用户下使用。')
    import base64
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    b = base64.b64decode(text)
    buf = ctypes.create_string_buffer(b)
    source, target = Blob(len(b), buf), Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('无法解密 API 密钥，请在设置中重新填写。')
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)
