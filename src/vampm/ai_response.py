"""Validate structured AI replies without repairing or saving partial content."""
import json
import re


class AIResponseError(ValueError):
    """Only malformed/incomplete model output is eligible for bounded retry."""
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def decode_json(text, *, stage='content', **details):
    if not isinstance(text, (str, bytes)) or not text.strip():
        raise AIResponseError('AI 返回空内容，尚未生成完整结果。', stage=stage, **details)
    if isinstance(text, str):
        text = text.strip().lstrip('\ufeff')
        # Accept a complete Markdown fence, never guess missing quotes/braces.
        fence = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', text, re.I)
        if fence:
            text = fence[1]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        position = ({'line': error.lineno, 'column': error.colno, 'position': error.pos}
                    if isinstance(error, json.JSONDecodeError) else {})
        raise AIResponseError('AI 返回的 JSON 不完整或格式错误，可能被截断；该回复未保存。',
                              stage=stage, **details, **position) from None


def completion_json(raw):
    body = decode_json(raw, stage='envelope')
    choices = body.get('choices') if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AIResponseError('API 未返回有效的模型结果。', stage='envelope')
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict):
        message = {}
    reason = choice.get('finish_reason')
    if reason == 'content_filter' or message.get('refusal'):
        raise ValueError('模型服务拒绝生成本次内容；该批未保存，不自动重试。')
    content = message.get('content')
    reasoning = message.get('reasoning_content')
    details = {'stage': 'content',
               'finish_reason': reason if reason in ('stop', 'length', 'max_tokens', 'tool_calls',
                    'insufficient_system_resource', 'aborted', None) else 'unknown',
               'content_chars': len(content) if isinstance(content, str) else 0,
               'reasoning_chars': len(reasoning) if isinstance(reasoning, str) else 0}
    usage = body.get('usage')
    if isinstance(usage, dict):
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            if type(usage.get(key)) is int:
                details[key] = usage[key]
        tokens = usage.get('completion_tokens_details')
        if isinstance(tokens, dict) and type(tokens.get('reasoning_tokens')) is int:
            details['reasoning_tokens'] = tokens['reasoning_tokens']
    if reason in ('length', 'max_tokens'):
        raise AIResponseError('AI 输出达到长度或上下文上限，结果可能被截断；该回复未保存。', **details)
    if reason in ('insufficient_system_resource', 'aborted'):
        raise AIResponseError('模型服务中断了生成，未收到完整结果。', **details)
    if reason not in (None, 'stop'):
        raise ValueError('模型未正常完成 JSON 生成；请检查所选模型是否支持此接口。')
    if not isinstance(content, str):
        raise AIResponseError('API 未返回有效的文本结果。', **details)
    return decode_json(content, **details)
