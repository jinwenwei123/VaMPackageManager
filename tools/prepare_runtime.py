"""Download the official embeddable runtime with a pinned SHA-256; no pip needed."""
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip'
SHA256 = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'


def main():
    print('Downloading official Python embedded runtime...',flush=True)
    with urllib.request.urlopen(URL,timeout=90) as response:
        data=response.read(20*1024*1024)
    if hashlib.sha256(data).hexdigest()!=SHA256:
        raise ValueError('Runtime SHA-256 mismatch')
    runtime=ROOT/'runtime'
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(runtime)
    (runtime/'python313._pth').write_text('python313.zip\n.\n../src\n',encoding='ascii')
    (runtime/'source.json').write_text(json.dumps({'url':URL,'sha256':SHA256},indent=2),encoding='utf-8')
    print('Runtime prepared:',runtime)


if __name__=='__main__': main()
