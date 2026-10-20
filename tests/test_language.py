import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from test_upgrade import Response
from vampm.agent import INSTRUCTIONS
from vampm.language import Language, output_instruction
from vampm.providers import AI
from vampm.server import Application
from vampm.util import Job


class LanguageTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put

    def test_language_is_global_and_switching_never_changes_resource_data(self):
        path = self.put('作者.人物.1', folder='中文目录')
        app = Application(self.game, self.home)
        app.c.scan(Job('scan'))
        app.c.label('作者.人物.1', '人物', '已有中文简介', tags=['可爱风', '长发'])
        app.agent._entry('user', '保留中文历史')
        app.agent._save()
        original = path.read_bytes()
        detail = app.c.detail('作者.人物.1')
        history = app.agent.path.read_bytes()
        registry = app.directories.file.read_bytes()
        app.post('/api/language', {'language': 'en'})
        self.assertEqual(app.c.detail('作者.人物.1'), detail)
        self.assertEqual(app.agent.path.read_bytes(), history)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(app.directories.file.read_bytes(), registry)
        self.assertEqual(Language(app.directories.data).get(), {'language': 'en'})
        other = self.root / '第二个游戏'
        (other / 'AddonPackages').mkdir(parents=True)
        result = app.post('/api/game-directories/add', {'path': str(other)})
        ident = result['items'][-1]['id']
        app.post('/api/game-directories/switch', {'id': ident})
        self.assertIs(app.active.language, app.language)
        self.assertEqual(app.active.ai.settings(True)['_output_language'], 'en')
        app.post('/api/language', {'language': 'zh-CN'})
        app.post('/api/game-directories/switch', {'id': 'original'})
        self.assertEqual(app.c.detail('作者.人物.1'), detail)
        self.assertEqual(app.agent.path.read_bytes(), history)
        self.assertEqual(path.read_bytes(), original)

    def test_validation_and_atomic_save_leave_previous_preference_intact(self):
        language = Language(self.c.data)
        language.save({'language': 'en'})
        before = language.file.read_bytes()
        for value in ('fr', None, [], {}, 1, True):
            with self.assertRaises(ValueError):
                language.save({'language': value})
        with mock.patch('vampm.language.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                language.save({'language': 'zh-CN'})
        self.assertEqual(language.get(), {'language': 'en'})
        self.assertEqual(language.file.read_bytes(), before)

    def test_unreadable_language_file_defaults_without_overwriting_it(self):
        file = self.c.data / 'language.json'
        file.write_text('{broken json', 'utf-8')
        self.assertEqual(Language(self.c.data).get(), {'language': 'zh-CN'})
        self.assertEqual(file.read_text('utf-8'), '{broken json')

    def test_api_generation_languages_targets_and_stable_prefix(self):
        app = Application(self.game, self.home)
        app.ai.save({'provider': 'compatible', 'summary_images': False})
        requests = []

        def respond(request, **kwargs):
            payload = json.loads(request.data); requests.append(payload)
            system = payload['messages'][0]['content']
            english = 'OUTPUT LANGUAGE: English' in system
            answer = {'items': [{'key': 'a.look.1', 'category': '人物',
                                'summary': 'A character appearance preset.' if english else '人物外观预设。',
                                'tags': ['Long hair', 'Fantasy'] if english else ['长发', '幻想风']}]}
            return Response(json.dumps({'choices': [{'message': {'content': json.dumps(answer)}}]}).encode())

        with mock.patch('vampm.providers.unprotect_secret', return_value='fixture'), \
                mock.patch('urllib.request.OpenerDirector.open', side_effect=respond):
            for language in ('en', 'zh-CN'):
                app.language.save({'language': language})
                for target in ('summary', 'tags', 'both'):
                    first = app.ai.classify_batch([{'key': 'a.look.1', 'name': '中文包名'}], Job('one'), target)
                    app.ai.classify_batch([{'key': 'a.look.1', 'name': '中文包名'}], Job('two'), target)
                    self.assertEqual(requests[-1]['messages'][0], requests[-2]['messages'][0])
                    self.assertIn('中文包名', requests[-1]['messages'][1]['content'])
                    if target != 'tags':
                        self.assertEqual(first['a.look.1']['category'], '人物')
                    if language == 'en':
                        if target != 'tags': self.assertIn('character', first['a.look.1']['summary'])
                        if target != 'summary': self.assertEqual(first['a.look.1']['tags'], ['Long hair', 'Fantasy'])
            app.ai.save({'summary_images': False})
        self.assertNotIn('_output_language', json.loads(app.ai.file.read_text('utf-8')))

    def test_generation_job_keeps_language_when_preference_changes_between_batches(self):
        app = Application(self.game, self.home)
        app.language.save({'language': 'en'})
        job = Job('batch')
        answer = {'items': [{'key': 'a', 'category': '人物', 'summary': 'Character preset.'}]}
        with mock.patch.object(app.ai, 'complete_json', return_value=answer) as generate:
            app.ai.classify_batch([{'key': 'a'}], job)
            app.language.save({'language': 'zh-CN'})
            app.ai.classify_batch([{'key': 'a'}], job)
        for call in generate.call_args_list:
            self.assertEqual(call.args[0]['_output_language'], 'en')
            self.assertIn('OUTPUT LANGUAGE: English', call.args[1])

    def test_chat_language_changes_preserve_api_history_and_machine_identifiers(self):
        app = Application(self.game, self.home)
        app.ai.save({'provider': 'compatible'})
        history = [{'role': 'user', 'content': '原来的中文问题'}, {'role': 'assistant', 'content': '原来的中文回答'}]
        app.agent.data.update(api_history=list(history), api_context_version=1)
        requests = []

        def respond(request, **kwargs):
            payload = json.loads(request.data); requests.append(payload)
            return Response(json.dumps({'choices': [{'message': {'content': 'New English reply.'}}]}).encode())

        app.language.save({'language': 'en'})
        with mock.patch('vampm.agent.unprotect_secret', return_value='fixture'), \
                mock.patch('urllib.request.OpenerDirector.open', side_effect=respond):
            app.agent._api_turn(app.ai.settings(True), '继续这个对话')
        self.assertIn('OUTPUT LANGUAGE: English', requests[0]['messages'][0]['content'])
        self.assertEqual(requests[0]['messages'][1:3], history)
        self.assertEqual(app.agent.data['api_history'][:2], history)
        instruction = requests[0]['messages'][0]['content']
        self.assertIn('AddonPackages/基础包', instruction)
        self.assertIn('["@selected"]', instruction)
        self.assertEqual(output_instruction(instruction, 'en'), instruction)

    def test_codex_reopens_model_thread_on_language_change_without_clearing_dialogue(self):
        app = Application(self.game, self.home)
        app.agent._entry('user', '保留此前对话')
        app.agent._entry('assistant', '此前中文答复')
        app.agent.data.update(thread_id='old-thread', provider='codex',
                              model=app.ai.settings()['codex_model'], output_language='zh-CN')
        app.language.save({'language': 'en'})
        captured = {}

        class FakeCodex:
            def __init__(self, *args): self.thread_id = None; self.process = None
            def connect_thread(self, thread, instructions, tools, cancel):
                captured.update(thread=thread, instructions=instructions)
                self.thread_id = 'new-thread'
                return self.thread_id
            def turn(self, text, cancel, emit, call):
                captured['text'] = text
                emit('delta', {'id': 'english-reply', 'text': 'New reply in English.'})
            def close(self): pass

        with mock.patch('vampm.agent.CodexChat', FakeCodex), \
                mock.patch.object(app.ai.models, 'reasoning', return_value=None):
            app.agent.send('继续')
            app.agent.worker.join(5)
        self.assertFalse(app.agent.busy())
        self.assertIsNone(captured['thread'])
        self.assertIn('OUTPUT LANGUAGE: English', captured['instructions'])
        self.assertIn('保留此前对话', captured['text'])
        self.assertEqual(app.agent.data['messages'][0]['text'], '保留此前对话')
        self.assertEqual(app.agent.data['messages'][1]['text'], '此前中文答复')

    def test_catalog_placeholders_and_generated_bundle(self):
        root = Path(__file__).resolve().parents[1]
        for file in ('en.json', 'messages.en.json'):
            catalog = json.loads((root / 'web/locales' / file).read_text('utf-8'))
            for source, target in catalog.items():
                self.assertEqual(set(re.findall(r'\{\d+\}', source)), set(re.findall(r'\{\d+\}', target)), source)
        spec = importlib.util.spec_from_file_location('build_locales', root / 'tools/build_locales.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertEqual((root / 'web/locales/en.js').read_text('utf-8'), module.build())
