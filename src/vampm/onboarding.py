"""Global first-run progress; skipping never selects or creates a game directory."""
from .util import atomic_json, read_json


class Onboarding:
    def __init__(self, directories):
        self.file = directories.data / 'onboarding.json'
        initial = 'done' if directories.value['items'] else 'game'
        self.value = read_json(self.file, {'stage': initial})
        if not isinstance(self.value, dict) or self.value.get('stage') not in ('game', 'ai', 'done'):
            raise ValueError('首次设置记录无效；原数据保留。')
        if not self.file.exists() and initial != 'done':
            self.set(initial)

    def get(self):
        return dict(self.value)

    def set(self, stage):
        self.value = {'stage': stage}
        atomic_json(self.file, self.value)
        return self.get()
