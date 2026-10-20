"""Synthetic catalog timings and disposable same-volume move benchmarks.

Run with runtime/python.exe tests/benchmark.py --label before|after.
All reads, scans and moves use generated fixtures; no local game data is opened.
"""
import argparse
import json
import platform
import re
import statistics
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest import mock

HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME / 'src'))
from vampm.catalog import Catalog
from vampm.operations import Operations
from vampm.util import Job


def measure(fn, repeats=7):
    values = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        values.append((time.perf_counter() - start) * 1000)
    return {'median_ms': round(statistics.median(values), 3),
            'max_ms': round(max(values), 3), 'runs_ms': [round(x, 3) for x in values]}, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--count', type=int, default=1000)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', args.label):
        parser.error('--label must contain 1–60 letters, digits, underscores or hyphens')
    if not 1 <= args.count <= 100000:
        parser.error('--count must be between 1 and 100000')
    report = {'label': args.label, 'platform': platform.platform(), 'python': sys.version,
              'fixture_files': args.count,
              'notes': 'Warm OS cache; fixture VARs are 32 KiB each; game-running checks mocked ONLY for disposable fixture operations. No real library is opened.'}
    output = HOME / 'test-results'
    output.mkdir(exist_ok=True)
    # Use the OS temp filesystem. WSL's mounted Windows drives may not support
    # renameat2(RENAME_NOREPLACE), which the Linux fixture backend requires.
    with tempfile.TemporaryDirectory(prefix='benchmark-') as temp:
        base = Path(temp)
        game = base / 'fixture-game'
        (game / 'AddonPackages').mkdir(parents=True)
        c = Catalog(game, base / 'fixture-tool')
        for i in range(args.count):
            folder = game / 'AddonPackages' / f'folder{i % 20}'
            folder.mkdir(exist_ok=True)
            with zipfile.ZipFile(folder / f'Bench.Item{i:05}.1.var','w') as z:
                z.writestr('meta.json', json.dumps({'creatorName':'Bench', 'packageName':f'Item{i:05}', 'dependencies':{}}))
                z.writestr('Saves/scene/fixture.json','{}')
                z.writestr('fixture.bin', bytes(32 * 1024))
        c.scan(Job('fixture'), deep=True)
        report['cold_catalog'], _ = measure(c.stats, 1)
        report['fixture_library'] = {'packages': args.count}
        report['status_warm'], _ = measure(c.stats, 15)
        report['search_warm'], _ = measure(lambda: c.list({'q':'Item', 'sort':'new'}), 15)
        report['page_warm'], _ = measure(lambda: c.list({'page':7}), 15)
        report['missing_cold'], _ = measure(c.missing, 1)
        report['missing_warm'], _ = measure(c.missing, 10)
        report['unchanged_scan'], scan = measure(lambda: c.scan(Job('benchmark')), 2)
        report['scan_result'] = scan
        print('Synthetic catalog benchmarks finished', flush=True)
        ops = Operations(c)
        keys = list(c.packages())
        with mock.patch('vampm.operations.require_game_closed'):
            report['move_plan'], plan = measure(lambda:ops.plan('organize',keys,Job('plan')),1)
            report['move_apply'], _ = measure(lambda:ops.apply(plan['id'],Job('apply')),1)
            report['history_warm'], _ = measure(ops.history,15)
            report['move_undo'], _ = measure(lambda:ops.undo(plan['id'],Job('undo')),1)
        assert c.stats()['files'] == args.count
        report['deep_metadata_preserved'] = all(p['deep'] for p in c.packages().values())
    (output / f'performance-{args.label}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({k:v for k,v in report.items() if isinstance(v,dict) and 'median_ms' in v}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
