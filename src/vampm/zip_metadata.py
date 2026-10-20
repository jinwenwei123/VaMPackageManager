"""Replace one ZIP member without decompressing/recompressing unrelated assets.

The undo record stores only the original metadata local record and ZIP directory.
Unchanged local records are copied verbatim, including data descriptors and ZIP64.
"""
import copy
import hashlib
import os
import zipfile

from .util import atomic_json, read_json


def transfer(src,dst,start,length,job):
    src.seek(start);digest=hashlib.sha256()
    while length:
        job.check();chunk=src.read(min(length,4*1024*1024))
        if not chunk:raise ValueError('ZIP 数据提前结束')
        dst.write(chunk);digest.update(chunk);length-=len(chunk)
    return digest.hexdigest()


def layout(z):
    entries=sorted(z.infolist(),key=lambda i:i.header_offset)
    if len({i.filename.casefold() for i in entries})!=len(entries):raise ValueError('ZIP 有重复内部路径，不能自动改写')
    if any(i.flag_bits & 1 for i in entries):raise ValueError('加密 ZIP 不支持自动修复')
    starts=[i.header_offset for i in entries]+[z.start_dir]
    if not entries or starts[0]<0 or any(a>=b for a,b in zip(starts,starts[1:])):raise ValueError('ZIP 内部偏移无效')
    return entries,starts


def rewrite(source,target,metadata,backup,job):
    """Prepare a new archive. Never replace source here; caller journals the swap."""
    backup.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(source) as src,open(source,'rb') as raw:
        entries,starts=layout(src)
        meta=next((i for i in entries if i.filename.casefold()=='meta.json'),None)
        if meta is None:raise ValueError('缺少 meta.json')
        saved={'prefix':starts[0],'entries':[]}
        with zipfile.ZipFile(target,'w',allowZip64=True) as out:
            out.comment=src.comment
            transfer(raw,out.fp,0,starts[0],job)
            for n,info in enumerate(entries):
                length=starts[n+1]-starts[n];offset=out.fp.tell();item=copy.copy(info)
                if info is meta:
                    with open(backup/'meta-record.bin','wb') as f:transfer(raw,f,starts[n],length,job)
                    out.start_dir=offset
                    out.writestr(item,metadata)
                    digest=None
                else:
                    digest=transfer(raw,out.fp,starts[n],length,job)
                    item.header_offset=offset
                    out.filelist.append(item);out.NameToInfo[item.filename]=item
                    out._didModify=True
                out.start_dir=out.fp.tell()
                saved['entries'].append({'name':info.filename,'length':length,'sha':digest})
        with open(backup/'directory.bin','wb') as f:transfer(raw,f,src.start_dir,source.stat().st_size-src.start_dir,job)
        atomic_json(backup/'layout.json',saved)
    # Verify byte-for-byte preservation of all untouched compressed records.
    with zipfile.ZipFile(target) as z,open(target,'rb') as raw:
        actual,starts=layout(z)
        if [i.filename for i in actual]!=[i['name'] for i in saved['entries']]:raise ValueError('ZIP 条目顺序改变')
        if z.read(meta.filename)!=metadata:raise ValueError('meta.json 写入验证失败')
        for n,item in enumerate(saved['entries']):
            if item['sha'] is None:continue
            length=starts[n+1]-starts[n]
            with open(os.devnull,'wb') as sink:digest=transfer(raw,sink,starts[n],length,job)
            if length!=item['length'] or digest!=item['sha']:raise ValueError('非元数据资源验证失败')
    with open(target,'rb+') as f:os.fsync(f.fileno())


def restore(source,target,backup,job):
    saved=read_json(backup/'layout.json')
    with zipfile.ZipFile(source) as z,open(source,'rb') as raw,open(target,'wb') as out:
        entries,starts=layout(z)
        if [i.filename for i in entries]!=[i['name'] for i in saved['entries']]:raise ValueError('ZIP 内容已经改变')
        transfer(raw,out,0,saved['prefix'],job)
        for n,item in enumerate(saved['entries']):
            if item['sha'] is None:
                p=backup/'meta-record.bin'
                with open(p,'rb') as f:transfer(f,out,0,p.stat().st_size,job)
            else:
                length=starts[n+1]-starts[n]
                digest=transfer(raw,out,starts[n],length,job)
                if length!=item['length'] or digest!=item['sha']:raise ValueError('修复后资源内容已变化')
        p=backup/'directory.bin'
        with open(p,'rb') as f:transfer(f,out,0,p.stat().st_size,job)
        out.flush();os.fsync(out.fileno())
