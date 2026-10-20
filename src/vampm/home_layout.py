"""Per-library home cards, ordered and validated independently of resource data."""
import threading
from .util import atomic_json, read_json

CARDS = ('directories','library','favorites','profiles','service','generation','health','usage','appearance','history',
         'recent','categories','pinned','assistant','storage','index')
DEFAULT = list(CARDS[:10])


class HomeLayout:
    def __init__(self, data):
        self.file = data / 'home-layout.json'
        self.lock = threading.Lock()
        saved = read_json(self.file, {})
        cards = saved.get('cards') if isinstance(saved, dict) else None
        self.cards = list(dict.fromkeys(c for c in cards if c in CARDS)) if isinstance(cards, list) else list(DEFAULT)

    def get(self):
        with self.lock: return {'cards': list(self.cards), 'available': list(CARDS)}

    def save(self, value):
        cards = value.get('cards')
        if (not isinstance(cards, list) or len(cards) > len(CARDS)
                or any(not isinstance(c, str) or c not in CARDS for c in cards) or len(set(cards)) != len(cards)):
            raise ValueError('主页卡片列表无效。')
        with self.lock:
            atomic_json(self.file, {'cards': cards})
            self.cards = list(cards)
            return self.get_unlocked()

    def get_unlocked(self):
        return {'cards': list(self.cards), 'available': list(CARDS)}
