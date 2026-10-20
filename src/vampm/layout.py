"""One physical home per VAR; content tags do not duplicate package bytes."""
import os
from pathlib import Path, PurePosixPath

from .util import safe_path


def package_path(package,row,favorite_root,favorite=None):
    favorite=package['favorite'] if favorite is None else favorite
    parts=([favorite_root] if favorite else [])+[package['category']]
    if len(package['files'])>1:
        parts.extend([package['pkgid'],'副本-'+str(row['id'])])
    return '/'.join(parts+[PurePosixPath(row['rel']).name])


def auxiliary_rows(catalog,job=None):
    known={(r['area'],r['rel'].casefold()) for r in catalog.rows()}
    for area in ('active','vault'):
        root=catalog.roots[area]
        if not root.is_dir():continue
        for parent,dirs,files in os.walk(root,followlinks=False):
            if job:job.check()
            dirs[:]=[d for d in dirs if not (Path(parent)/d).is_symlink()
                     and not (hasattr(Path(parent)/d,'is_junction') and (Path(parent)/d).is_junction())]
            for name in files:
                rel=os.path.relpath(os.path.join(parent,name),root).replace('\\','/')
                if (area,rel.casefold()) in known:continue
                path=Path(parent)/name
                if path.is_symlink():continue
                path=safe_path(root,rel)
                if not path.is_file():continue
                st=path.stat()
                yield {'id':None,'area':area,'rel':rel,'size':st.st_size,'mtime':st.st_mtime_ns,'auxiliary':True}
