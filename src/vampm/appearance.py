import threading

from .util import atomic_json, read_json


PALETTES = ('forest', 'ocean', 'violet', 'rose', 'amber', 'graphite')
MODES = ('light', 'dark', 'system')
DEFAULTS = {'palette': 'forest', 'mode': 'light'}


class Appearance:
    """App-owned preferences survive the launcher's changing localhost port."""
    def __init__(self, data):
        self.file = data / 'appearance.json'
        self.lock = threading.Lock()
        saved = read_json(self.file, {})
        if not isinstance(saved, dict): saved = {}
        self.value = {k: saved.get(k) if saved.get(k) in choices else DEFAULTS[k]
                      for k, choices in (('palette', PALETTES), ('mode', MODES))}

    def get(self):
        with self.lock: return dict(self.value)

    def save(self, data):
        with self.lock:
            value = dict(self.value)
            for key, choices in (('palette', PALETTES), ('mode', MODES)):
                if key not in data: continue
                if data[key] not in choices: raise ValueError('未知的界面配色或明暗模式')
                value[key] = data[key]
            atomic_json(self.file, value)
            self.value = value
            return dict(value)
