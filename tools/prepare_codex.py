"""Install an app-local official Windows Codex build, verified against pinned npm SRI."""
import argparse
import base64
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
VERSION = '0.163.0'
URL = 'https://registry.npmjs.org/@openai/codex/-/codex-0.163.0-win32-x64.tgz'
SHA512 = 'QQGQ/q9dCJ5C5jqhgFrwycpWG3szIyFNThMiKl6hYtu6N/eM3Qd7StesgnCFAfzVED8IkE3N3oH71szzcludqw=='


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,help='Use a previously downloaded archive; still verify SRI.')
    args=parser.parse_args()
    runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True)
    target=runtime/'codex'
    if target.exists():
        raise ValueError('runtime/codex already exists. Stop the app and move it aside before rebuilding.')
    with tempfile.TemporaryDirectory(prefix='codex-install-',dir=runtime) as temp:
        work=Path(temp);archive=args.archive or work/'codex.tgz'
        if not args.archive:
            print('Downloading official Codex '+VERSION+'...',flush=True)
            with urllib.request.urlopen(URL,timeout=60) as response, archive.open('xb') as out:
                shutil.copyfileobj(response,out,1024*1024)
        digest=hashlib.sha512()
        with archive.open('rb') as stream:
            while block:=stream.read(1024*1024): digest.update(block)
        if base64.b64encode(digest.digest()).decode()!=SHA512:
            raise ValueError('Codex archive SRI verification failed; nothing installed.')
        stage=work/'package';stage.mkdir()
        with tarfile.open(archive,'r:gz') as source:
            for member in source:
                rel=PurePosixPath(member.name)
                if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='package':
                    raise ValueError('Unexpected archive path')
                dest=stage.joinpath(*rel.parts[1:])
                if member.isdir(): dest.mkdir(parents=True,exist_ok=True)
                elif member.isfile():
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    with source.extractfile(member) as inp, dest.open('xb') as out:
                        shutil.copyfileobj(inp,out,1024*1024)
                else: raise ValueError('Unexpected archive entry type')
        exe=stage/'vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        if not exe.is_file(): raise ValueError('Expected Codex executable missing')
        (stage/'source.json').write_text(json.dumps({'version':VERSION,'url':URL,
            'integrity':'sha512-'+SHA512,'license':'Apache-2.0'},indent=2),encoding='utf-8')
        stage.rename(target)
    print('Verified Codex installed in',target)


if __name__=='__main__': main()
