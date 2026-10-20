"""First-run entrypoint must not guess a game root or create game folders."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from vampm.server import Application
from vampm.util import read_json


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'manager'
        self.home.mkdir()

    def app(self, game=None):
        app = Application(game, self.home)
        self.addCleanup(lambda: app.active.ai.close())
        self.addCleanup(lambda: app.active.agent.close())
        return app

    def test_actual_entrypoint_never_adopts_parent_even_when_it_contains_addonpackages(self):
        # A real --home launch follows the same no-game path as the EXE.
        (self.root / 'AddonPackages').mkdir()
        sentinel = self.root / 'AddonPackages' / 'keep.txt'
        sentinel.write_text('original')
        script = Path(__file__).resolve().parents[1] / 'src/main.py'
        child = subprocess.Popen([sys.executable, '-B', str(script), '--home', str(self.home), '--no-browser'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        state = None
        try:
            for _ in range(150):
                if (self.home / 'data/server.json').exists():
                    state = read_json(self.home / 'data/server.json'); break
                if child.poll() is not None:
                    self.fail('Entrypoint exited: ' + child.stderr.read().decode(errors='replace'))
                time.sleep(.1)
            self.assertIsNotNone(state)
            req = urllib.request.Request(state['url'] + '/api/status', headers={'X-Session-Token': state['token']})
            with urllib.request.urlopen(req, timeout=10) as response: status = json.load(response)
            self.assertIsNone(status['workspace']['id'])
            self.assertEqual(status['game_directories']['items'], [])
            self.assertEqual(status['onboarding']['stage'], 'game')
            self.assertFalse((self.root / '.PackageManagerStorage').exists())
            self.assertFalse((self.root / '.DisabledPackages').exists())
            self.assertEqual(sentinel.read_text(), 'original')
        finally:
            if state:
                req = urllib.request.Request(state['url'] + '/api/shutdown', data=b'{}',
                        headers={'Content-Type': 'application/json', 'X-Session-Token': state['token']})
                urllib.request.urlopen(req, timeout=10).close()
            else: child.terminate()
            child.wait(timeout=15)
            child.stderr.close()

    def test_skip_persists_and_game_actions_are_blocked_without_any_game_folders(self):
        app = self.app()
        self.assertIsNone(app.workspace_id)
        self.assertEqual(app.post('/api/onboarding', {'action': 'skip_game'})['stage'], 'ai')
        self.assertEqual(app.post('/api/onboarding', {'action': 'finish'})['stage'], 'done')
        for route in ['/api/scan', '/api/launch', '/api/agent/send', '/api/ai-queue/start']:
            with self.assertRaisesRegex(ValueError, '选择游戏目录'): app.post(route, {})
        restored = self.app()
        self.assertEqual(restored.get('/api/status', {})['onboarding']['stage'], 'done')
        self.assertIsNone(restored.workspace_id)
        self.assertEqual(list(self.root.iterdir()), [self.home])
        self.assertFalse(any(p.name in ['AddonPackages', '.PackageManagerStorage', '.DisabledPackages']
                             for p in self.home.rglob('*')))

    def test_select_requires_real_game_and_inherits_preconfigured_ai_without_scanning(self):
        app = self.app()
        app.post('/api/settings', {'provider': 'compatible', 'base_url': 'https://example.invalid/v1',
                                  'api_model': 'demo-model', 'api_summary_model': 'demo-model'})
        invalid = self.root / 'not-a-game'; invalid.mkdir()
        with self.assertRaises(ValueError): app.post('/api/onboarding', {'action': 'select_game', 'path': str(invalid)})
        self.assertEqual(list(invalid.iterdir()), [])
        game = self.root / 'Chosen game'; (game / 'AddonPackages').mkdir(parents=True)
        sentinel = game / 'AddonPackages' / 'untouched.txt'; sentinel.write_text('keep')
        result = app.post('/api/onboarding', {'action': 'select_game', 'path': str(game)})
        self.assertEqual(result['stage'], 'ai')
        self.assertEqual(app.active.c.game, game)
        self.assertEqual(app.active.c.stats()['files'], 0)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertTrue((game / '.PackageManagerStorage').is_dir())
        self.assertFalse((self.root / 'AddonPackages').exists())
        self.assertEqual(app.active.ai.settings(True)['api_model'], 'demo-model')
        restored = self.app()
        self.assertEqual(restored.active.c.game, game)
        self.assertEqual(restored.get('/api/status', {})['onboarding']['stage'], 'ai')

    def test_existing_registry_and_user_settings_are_preserved_without_wizard(self):
        game = self.root / 'Existing'; (game / 'AddonPackages').mkdir(parents=True)
        app = self.app(game)
        app.ai.save({'provider': 'deepseek'})
        before = (self.home / 'data/game-directories.json').read_bytes()
        restored = self.app()
        self.assertEqual(restored.workspace_id, 'original')
        self.assertEqual(restored.c.game, game)
        self.assertEqual(restored.ai.settings(True)['provider'], 'deepseek')
        self.assertEqual(restored.get('/api/status', {})['onboarding']['stage'], 'done')
        self.assertEqual((self.home / 'data/game-directories.json').read_bytes(), before)


if __name__ == '__main__': unittest.main()
