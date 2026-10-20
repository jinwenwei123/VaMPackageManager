"""Prioritized VAR preview extraction with a bounded, fingerprint-aware cache."""
import collections
import threading
import zipfile
from pathlib import PurePosixPath


def cover_candidates(infos):
    names={i.filename.replace('\\','/').casefold() for i in infos}
    scored=[]
    for item in infos:
        name=item.filename.replace('\\','/').casefold();path=PurePosixPath(name)
        if path.suffix not in ('.jpg','.jpeg','.png') or not 0<item.file_size<=6*1024*1024: continue
        if any(x in name for x in ('/textures/','/texture/','/decals/','/icons/')): continue
        stem=str(path.with_suffix(''))
        paired=any(stem+ext in names for ext in ('.json','.vap','.vam'))
        preview=any(w in path.stem for w in ('preview','thumbnail','cover','screenshot'))
        if name.startswith('saves/scene/') and paired: rank=0
        elif '/appearance/' in name and paired: rank=1
        elif preview: rank=2
        elif paired and ('/clothing/' in name or '/hair/' in name): rank=3
        elif name.startswith('saves/scene/'): rank=4
        elif '/appearance/' in name: rank=5
        elif len(path.parts)==1: rank=6
        else: continue  # Do not mistake an arbitrary skin map for a preview.
        scored.append((rank,len(name),name,item))
    return [x[-1] for x in sorted(scored,key=lambda x:x[:3])]


def read_cover(path):
    """Read one actual preview, never extract archive paths into the filesystem."""
    try:
        with zipfile.ZipFile(path) as z:
            for item in cover_candidates(z.infolist()):
                try: data=z.read(item)
                except (OSError, ValueError, RuntimeError, zipfile.BadZipFile): continue
                mime='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None
                if mime: return data,mime,item.filename
    except (OSError, ValueError, zipfile.BadZipFile): pass
    return None


class Covers:
    def __init__(self,catalog):
        self.c=catalog;self.cache=collections.OrderedDict();self.bytes=0
        self.lock=threading.RLock()

    def get(self,key):
        p=self.c.packages().get(key.casefold())
        if not p: return None
        row=p['files'][0];path=self.c.path(row)
        try: stat=path.stat()
        except OSError: return None
        cache_key=(str(path),stat.st_size,stat.st_mtime_ns)
        with self.lock:
            if cache_key in self.cache:
                self.cache.move_to_end(cache_key);return self.cache[cache_key]
        preview=read_cover(path)
        result=preview[:2] if preview else None
        with self.lock:
            if cache_key in self.cache: return self.cache[cache_key]
            self.cache[cache_key]=result;self.bytes+=len(result[0]) if result else 0
            while len(self.cache)>256 or self.bytes>48*1024*1024:
                _,old=self.cache.popitem(last=False);self.bytes-=len(old[0]) if old else 0
        return result
