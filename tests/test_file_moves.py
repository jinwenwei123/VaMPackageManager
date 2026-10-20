import errno
import os
import tempfile
import unittest
from pathlib import Path

from vampm.operations import move_no_replace


class CrossVolumeMoves(unittest.TestCase):
    def test_real_cross_volume_file_keeps_contents_and_timestamp(self):
        if os.name == 'nt':
            other = Path(__file__).resolve().parents[1] / 'test-results'
            other.mkdir(exist_ok=True)
        else:
            other = Path('/dev/shm')
            if not other.is_dir(): self.skipTest('Second filesystem unavailable')
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory(dir=other) as b:
            source, target = Path(a) / 'sample.var', Path(b) / 'sample.var'
            source.write_bytes(b'cross-volume fixture' * 8192)
            if source.stat().st_dev == Path(b).stat().st_dev:
                self.skipTest('A second filesystem is required')
            before = source.read_bytes(), source.stat().st_mtime_ns
            move_no_replace(source, target)
            self.assertFalse(source.exists())
            self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'a', Path(folder) / 'b'
            source.write_bytes(b'source'); target.write_bytes(b'keep')
            with self.assertRaises((ValueError, FileExistsError)):
                move_no_replace(source, target)
            self.assertEqual(source.read_bytes(), b'source')
            self.assertEqual(target.read_bytes(), b'keep')


class Interrupted(BaseException):
    pass


class RecoverableMoves(unittest.TestCase):
    def setUp(self):
        from vampm import file_moves
        from unittest import mock
        self.moves = file_moves
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.source.mkdir(); (self.source / 'empty').mkdir()
        (self.source / 'a').write_bytes(b'first'); (self.source / 'b').write_bytes(b'second')
        rename = file_moves._rename
        def across(source, target):
            if source == self.source: raise OSError(errno.EXDEV, 'fixture cross volume')
            return rename(source, target)
        patcher = mock.patch.object(file_moves, '_rename', side_effect=across)
        patcher.start(); self.addCleanup(patcher.stop)

    def assert_original(self):
        self.assertEqual((self.source / 'a').read_bytes(), b'first')
        self.assertEqual((self.source / 'b').read_bytes(), b'second')
        self.assertTrue((self.source / 'empty').is_dir())

    def assert_complete(self):
        self.assertFalse(self.source.exists())
        self.assertEqual((self.target / 'a').read_bytes(), b'first')
        self.assertEqual((self.target / 'b').read_bytes(), b'second')
        self.assertTrue((self.target / 'empty').is_dir())
        self.assertEqual(list(self.root.glob('.vampm-transfer-*')), [])

    def test_directory_with_empty_child(self):
        move_no_replace(self.source, self.target)
        self.assert_complete()

    def test_insufficient_space_preserves_source(self):
        from unittest import mock
        with mock.patch.object(self.moves.shutil, 'disk_usage', return_value=type('Space', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, '空间不足'): move_no_replace(self.source, self.target)
        self.assert_original(); self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.vampm-transfer-*')), [])

    def test_cancel_before_publication_cleans_only_temporary_copy(self):
        from unittest import mock
        from vampm.util import Cancelled
        with mock.patch.object(self.moves, 'sha256', side_effect=Cancelled('cancel')):
            with self.assertRaises(Cancelled): move_no_replace(self.source, self.target)
        self.assert_original(); self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.vampm-transfer-*')), [])

    def test_corruption_during_copy_does_not_publish(self):
        from unittest import mock
        with mock.patch.object(self.moves, 'sha256', return_value='wrong hash'):
            with self.assertRaisesRegex(ValueError, '校验'): move_no_replace(self.source, self.target)
        self.assert_original(); self.assertFalse(self.target.exists())

    def test_crash_mid_copy_can_resume_or_abort(self):
        from unittest import mock
        for finish in (False, True):
            with self.subTest(finish=finish):
                with mock.patch.object(self.moves, 'sha256', side_effect=Interrupted):
                    with self.assertRaises(Interrupted): move_no_replace(self.source, self.target)
                self.assert_original()
                self.moves.recover_move(self.source, self.target, finish=finish)
                if finish: self.assert_complete()
                else:
                    self.assert_original(); self.assertFalse(self.target.exists())
                    self.assertEqual(list(self.root.glob('.vampm-transfer-*')), [])

    def test_crash_after_publish_is_recovered_before_source_cleanup(self):
        from unittest import mock
        with mock.patch.object(self.moves, '_finish', side_effect=Interrupted):
            with self.assertRaises(Interrupted): move_no_replace(self.source, self.target)
        self.assert_original(); self.assertTrue(self.target.exists())
        self.moves.recover_move(self.source, self.target)
        self.assert_complete()

    def test_crash_during_cleanup_recovers_remaining_files(self):
        from unittest import mock
        unlink = Path.unlink
        def crash(path, *args, **kwargs):
            if path == self.source / 'b': raise Interrupted()
            return unlink(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', crash):
            with self.assertRaises(Interrupted): move_no_replace(self.source, self.target)
        self.assertFalse((self.source / 'a').exists())
        self.moves.recover_move(self.source, self.target, finish=True)
        self.assert_complete()

    def test_changed_published_target_never_deletes_original(self):
        from unittest import mock
        with mock.patch.object(self.moves, '_finish', side_effect=Interrupted):
            with self.assertRaises(Interrupted): move_no_replace(self.source, self.target)
        (self.target / 'a').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '校验'): self.moves.recover_move(self.source, self.target)
        self.assert_original(); self.assertTrue(self.moves.receipt_path(self.target).exists())

    def test_changed_source_preserves_both_copies(self):
        from unittest import mock
        digest = self.moves.sha256
        def mutate(path, job=None):
            value = digest(path, job)
            (self.source / 'a').write_bytes(b'new source')
            return value
        with mock.patch.object(self.moves, 'sha256', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, '变化'): move_no_replace(self.source, self.target)
        self.assertEqual((self.source / 'a').read_bytes(), b'new source')
        self.assertTrue(self.moves.receipt_path(self.target).exists())
        self.assertFalse(self.target.exists())

    def test_destination_created_during_copy_is_not_overwritten(self):
        from unittest import mock
        digest = self.moves.sha256
        def collide(path, job=None):
            self.target.mkdir(exist_ok=True)
            (self.target / 'keep').write_bytes(b'other writer')
            return digest(path, job)
        with mock.patch.object(self.moves, 'sha256', side_effect=collide):
            with self.assertRaises(FileExistsError): move_no_replace(self.source, self.target)
        self.assert_original(); self.assertEqual((self.target / 'keep').read_bytes(), b'other writer')
        self.assertEqual(list(self.root.glob('.vampm-transfer-*')), [])

    def test_unreadable_subtree_cannot_be_silently_skipped(self):
        from unittest import mock
        def walk(*args, **kwargs):
            kwargs['onerror'](PermissionError('unreadable directory'))
            return iter(())
        with mock.patch.object(self.moves.os, 'walk', side_effect=walk):
            with self.assertRaises(PermissionError): move_no_replace(self.source, self.target)
        self.assert_original(); self.assertFalse(self.target.exists())
