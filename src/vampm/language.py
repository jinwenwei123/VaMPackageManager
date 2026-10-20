"""Global display language; independent of browser origins and game workspaces."""
import threading

from .util import atomic_json, read_json


def output_instruction(instruction, language):
    """Keep stable prefixes per language and preserve machine-readable identifiers."""
    if '\n\nOUTPUT LANGUAGE:' in instruction or '\n\n输出语言：' in instruction:
        return instruction
    if language != 'en':
        return instruction + '\n\n输出语言：简体中文。新简介、内容标签和面向用户的回复使用中文；包 key、分类枚举、文件名和路径原样保留，不翻译或改名。'
    # Remove conflicting Chinese-only wording in existing task instructions.
    for source, target in (
        ('中文内容编辑', '英文内容编辑'), ('中文资源管理助手', '英文资源管理助手'),
        ('用中文清楚报告', '用英文清楚报告'), ('中文简介', '英文简介'),
        ('中文上下文摘要', '英文上下文摘要'), ('中文对话预设编辑', '英文对话预设编辑'),
        ('自然中文', '自然英文'), ('约2000中文字', '约1500个英文单词'),
    ):
        instruction = instruction.replace(source, target)
    return instruction + (
        '\n\nOUTPUT LANGUAGE: English. Write all new summaries, content tags, '
        'preset drafts, explanations and user-facing replies in natural English, '
        'even when the input, existing descriptions or previous conversation are Chinese. '
        'Translate suggested tag vocabulary into concise English equivalents; do not copy '
        'Chinese tag spellings into new English tags. Each tag must be 1–30 characters. '
        'Use "Adult themes" for 成人向. '
        'Return JSON field names and category enum values exactly as specified: category values '
        'remain the original Chinese identifiers. Preserve package keys, proper names, '
        'filenames and paths exactly. Never rename or move a file or folder merely to translate it. '
        'Do not translate or overwrite existing stored content unless the user explicitly requests generation or editing.'
    )


class Language:
    def __init__(self, data):
        self.file = data / 'language.json'
        self.lock = threading.Lock()
        try:
            saved = read_json(self.file, {})
        except (ValueError, OSError):
            # A display preference must not prevent access to game data.
            saved = {}
        value = saved.get('language') if isinstance(saved, dict) else None
        self.value = value if value in ('zh-CN', 'en') else 'zh-CN'

    def get(self):
        with self.lock:
            return {'language': self.value}

    def save(self, data):
        value = data.get('language')
        if value not in ('zh-CN', 'en'):
            raise ValueError('Unsupported interface language')
        with self.lock:
            atomic_json(self.file, {'language': value})
            self.value = value
            return {'language': value}
