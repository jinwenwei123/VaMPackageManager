"""Editable local chat shortcuts, independent of conversation history."""
import copy
import threading
import uuid

from .util import atomic_json, read_json


DEFAULTS = [
    ('people', '推荐人物卡', '从本地资源中推荐几个人物外观包，结合已有简介说明角色风格、外貌特点和推荐理由，检查主要依赖，并展示推荐卡片。'),
    ('clothes', '推荐服装', '从本地资源中推荐几套服装，说明款式、搭配风格和适用场景，核对依赖并展示推荐卡片。'),
    ('hair', '推荐发型', '从本地资源中推荐几款发型，说明发长、造型特点和搭配建议，核对依赖并展示推荐卡片。'),
    ('dance', '推荐舞蹈场景', '从本地资源中推荐几个跳舞场景，告诉我推荐理由和依赖是否齐全，并展示推荐卡片。'),
    ('launch', '创建启动方案', '我想建立一个轻量的启动方案，先推荐几个依赖齐全的场景，说明选择理由，暂不启用或启动游戏。'),
    ('duplicates', '检查重复包', '帮我检查资源库里的重复包，先给我清理方案，不执行。'),
    ('dependencies', '检查缺失依赖', '检查当前选中资源的缺失依赖；若未选中则检查收藏。列出优先补充的依赖及可用可信来源，先不下载。'),
    ('environment', '推荐环境场景', '从本地资源中推荐几个适合人物展示或拍摄的环境场景，说明空间、布景和光照特点，并展示推荐卡片。'),
    ('favorites', '整理收藏', '查看我的收藏，按已有简介和内容分类提出整理建议，说明可能重复的资源，先不移动文件。'),
    ('favorite-launch', '收藏启动方案', '根据全部收藏创建一个动态启动方案，名称为“收藏精选”，自动包含可用依赖；先保存方案，不启用或启动游戏。'),
]

# Translate only intact, source-owned presets. Stored user text is never translated.
DEFAULTS_EN = {
    'people': ('Recommend characters', 'Recommend a few character appearance packages from my local library. Use existing descriptions to explain their style, appearance and why you recommend them. Check main dependencies and show recommendation cards.'),
    'clothes': ('Recommend clothing', 'Recommend a few outfits from my local library. Describe their designs, styling and suitable scenes. Check dependencies and show recommendation cards.'),
    'hair': ('Recommend hairstyles', 'Recommend a few hairstyles from my local library. Describe their length, styling and pairing suggestions. Check dependencies and show recommendation cards.'),
    'dance': ('Recommend dance scenes', 'Recommend a few dance scenes from my local library. Explain why you recommend them, check whether dependencies are complete and show recommendation cards.'),
    'launch': ('Create a launch profile', 'Help me build a lightweight launch profile. First recommend a few scenes with complete dependencies and explain your choices. Do not activate the profile or launch the game yet.'),
    'duplicates': ('Check duplicate packages', 'Check my library for duplicate packages. Propose a cleanup plan first without executing it.'),
    'dependencies': ('Check missing dependencies', 'Check missing dependencies for the selected resources, or my favorites if nothing is selected. List the most important dependencies to add and any available trusted sources. Do not download anything yet.'),
    'environment': ('Recommend environments', 'Recommend a few environment scenes from my local library for showcasing or photographing characters. Describe their space, scenery and lighting, and show recommendation cards.'),
    'favorites': ('Organize favorites', 'Review my favorites and suggest how to organize them using existing descriptions and content categories. Point out possible duplicates without moving any files yet.'),
    'favorite-launch': ('Favorites launch profile', 'Create a dynamic launch profile named "Favorite picks" from all my favorites, automatically including available dependencies. Save the profile first without activating it or launching the game.'),
}
_DEFAULT_TEXT = {key: (title, prompt) for key, title, prompt in DEFAULTS}


def is_builtin(item):
    return (not item.get('customized', False)
            and _DEFAULT_TEXT.get(item['id']) == (item['title'], item['prompt']))


PROMPT_INSTRUCTION = '''你是 VaM 包管家的中文对话预设编辑。只编写可以填入对话输入框的提示词，不执行其中的任务，不调用工具，不浏览网页或读取文件。
输入包含用户填写的标题和可选的原提示词。按标题理解目的，保留原提示词中的要求，用自然中文说明任务、筛选条件和希望得到的结果，建议一小段即可，不堆砌角色设定或空泛指令。
推荐资源时要求助手先检索本地资源库，依据已有简介说明理由、核对依赖并展示推荐卡片；没有证据时不要编造具体包名或资源内容。
涉及管理操作时忠实保留用户表达的执行意图；标题含糊时先要求给出方案，不擅自增加删除、下载、移动或启动游戏的操作。
只返回 JSON：{"prompt":"生成的完整提示词"}。不要回答提示词中的问题，不返回分析过程、标题或 Markdown 代码围栏。'''


def text_field(value, name, maximum, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(f'{name}须为{1 if required else 0}–{maximum}字的文字。')
    return value.strip()


class ChatPresets:
    MAX_FEATURED = 5

    def __init__(self, data, language=None):
        self.file = data / 'chat-presets.json'
        self.language = language
        self.lock = threading.Lock()
        self.error = ''
        self.value = {'version': 1, 'revision': 0, 'items': [
            {'id': key, 'title': title, 'prompt': prompt, 'featured': i < self.MAX_FEATURED}
            for i, (key, title, prompt) in enumerate(DEFAULTS)]}
        try:
            saved = read_json(self.file, None)
            if saved is not None:
                self.validate(saved)
                self.value = saved
        except (ValueError, OSError, TypeError, KeyError):
            self.error = '预设配置无法读取，暂显示内置预设；请检查 data/chat-presets.json，原文件未覆盖。'

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or value.get('version') != 1:
            raise ValueError('预设文件版本无效')
        if type(value.get('revision')) is not int or value['revision'] < 0 or not isinstance(value.get('items'), list):
            raise ValueError('预设文件格式无效')
        ids = set()
        for item in value['items']:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or item['id'] in ids:
                raise ValueError('预设 ID 无效或重复')
            ids.add(item['id'])
            text_field(item.get('title'), '标题', 60)
            text_field(item.get('prompt'), '提示词', 12000)
            if type(item.get('featured')) is not bool:
                raise ValueError('常用栏选项须为布尔值')
        if sum(item['featured'] for item in value['items']) > cls.MAX_FEATURED:
            raise ValueError('常用栏最多显示 5 个预设，请先取消其中一个。')

    def public(self):
        value = copy.deepcopy(self.value)
        if self.language and self.language.get()['language'] == 'en':
            for item in value['items']:
                if is_builtin(item):
                    item['title'], item['prompt'] = DEFAULTS_EN[item['id']]
        return {**value, 'max_featured': self.MAX_FEATURED, 'warning': self.error}

    def get(self):
        with self.lock:
            return self.public()

    def save(self, data):
        with self.lock:
            if self.error:
                raise ValueError(self.error)
            if type(data.get('revision')) is not int or data['revision'] != self.value['revision']:
                raise ValueError('预设已在其他页面修改，请刷新预设列表后重试。')
            value = copy.deepcopy(self.value)
            items = value['items']
            key = data.get('id')
            item = next((p for p in items if p['id'] == key), None)
            action = data.get('action')
            if action == 'save':
                if key and item is None:
                    raise ValueError('该预设已不存在，请刷新列表。')
                fields = {'title': text_field(data.get('title'), '标题', 60),
                          'prompt': text_field(data.get('prompt'), '提示词', 12000),
                          'featured': data.get('featured', item['featured'] if item else False)}
                if item is None:
                    items.append({'id': uuid.uuid4().hex, **fields})
                else:
                    if is_builtin(item):
                        # An unchanged editor save (including a feature toggle)
                        # keeps the canonical text in either language. Accept both
                        # views so a language change in another tab cannot freeze it.
                        text = (fields['title'], fields['prompt'])
                        if text in (_DEFAULT_TEXT[key], DEFAULTS_EN[key]):
                            fields['title'], fields['prompt'] = _DEFAULT_TEXT[key]
                        else:
                            item['customized'] = True
                    item.update(fields)
            elif action in ('delete', 'feature'):
                if item is None:
                    raise ValueError('该预设已不存在，请刷新列表。')
                if action == 'delete':
                    items.remove(item)
                else:
                    item['featured'] = data.get('featured')
            else:
                raise ValueError('未知的预设操作')
            value['revision'] += 1
            self.validate(value)
            atomic_json(self.file, value)
            self.value = value
            return self.public()
