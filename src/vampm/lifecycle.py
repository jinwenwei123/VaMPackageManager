"""Keep the local service alive only while a browser page owns a connection."""
import re
import threading
import time


class PageSessions:
    def __init__(self, on_empty, grace=5, startup=90, clock=time.monotonic):
        self.on_empty, self.grace, self.startup, self.clock = on_empty, grace, startup, clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.deadline = clock() + startup
        self.stopping = threading.Event()
        self.worker = None

    def open(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', ident):
            raise ValueError('页面连接标识无效')
        with self.lock:
            if self.stopping.is_set(): raise ValueError('包管家正在退出，请重新双击 EXE 打开。')
            old = self.sessions.get(ident)
            if old: old.set()
            ticket = threading.Event()
            self.sessions[ident] = ticket
            self.deadline = None
            return ticket

    def close(self, ident, ticket=None):
        with self.lock:
            current = self.sessions.get(ident)
            if current is not None and (ticket is None or current is ticket):
                current.set()
                del self.sessions[ident]
                if not self.sessions: self.deadline = self.clock() + self.grace

    def launching(self):
        with self.lock:
            if self.stopping.is_set(): raise ValueError('正在退出，请稍后重新打开。')
            if not self.sessions: self.deadline = self.clock() + self.startup

    def check(self):
        with self.lock:
            if self.stopping.is_set() or self.sessions or self.deadline is None or self.clock() < self.deadline:
                return False
            self.stopping.set()
        self.on_empty()
        return True

    def start(self):
        if self.worker: return
        def watch():
            while not self.stopping.wait(.25):
                if self.check(): break
        self.worker = threading.Thread(target=watch, daemon=True)
        self.worker.start()

    def stop(self):
        with self.lock:
            self.stopping.set()
            for ticket in self.sessions.values(): ticket.set()
            self.sessions.clear()
