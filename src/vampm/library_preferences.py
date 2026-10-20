import threading

from .util import atomic_json, read_json


CHOICES = {'view': ('list', 'grid'), 'sort': ('name', 'new', 'size')}


class LibraryPreferences:
    """Remember presentation only; search, status filters and pages remain transient."""
    def __init__(self, data):
        self.file = data / 'library-preferences.json'
        self.lock = threading.Lock()
        try: saved = read_json(self.file, {})
        except (ValueError, OSError): saved = {}
        if not isinstance(saved, dict): saved = {}
        self.value = {key: saved.get(key) if saved.get(key) in choices else choices[0]
                      for key, choices in CHOICES.items()}

    def get(self):
        with self.lock: return dict(self.value)

    def save(self, data):
        with self.lock:
            value = dict(self.value)
            for key, choices in CHOICES.items():
                if key not in data: continue
                if data[key] not in choices: raise ValueError('未知的资源展示方式或排序')
                value[key] = data[key]
            atomic_json(self.file, value)
            self.value = value
            return dict(value)
