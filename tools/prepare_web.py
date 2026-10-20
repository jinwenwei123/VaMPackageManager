"""Vendor pinned Markdown libraries for a fully offline portable UI (npm ci first)."""
from pathlib import Path
import shutil

root=Path(__file__).resolve().parents[1]
dest=root/'web'/'vendor'
dest.mkdir(exist_ok=True)
for package,source,target in [('marked','lib/marked.umd.js','marked.js'),
                              ('dompurify','dist/purify.min.js','purify.js')]:
    base=root/'node_modules'/package
    shutil.copyfile(base/source,dest/target)
    license=next(base.glob('LICENSE*'))
    shutil.copyfile(license,dest/(package+'-LICENSE.txt'))
print('Vendored pinned marked and DOMPurify with licenses.')
