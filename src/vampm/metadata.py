"""Read VaM metadata and bounded semantic hints without changing archive contents."""
import collections
import json
import re
import zipfile
from pathlib import PurePosixPath

REVISION = 2


def load_metadata(raw, return_text=False):
    text=raw.decode('utf-8-sig') if isinstance(raw,bytes) else raw.lstrip('\ufeff')
    warnings=[]
    try:
        value=json.loads(text,strict=False)
    except json.JSONDecodeError:
        # Some published VARs contain unescaped prose in these top-level strings.
        # Recover prose only; never repair creator/package identity or dependency keys.
        matches=list(re.finditer(r'(?m)^([ \t]*)"(description|credits|instructions)"\s*:\s*"',text))
        for m in reversed(matches):
            nxt=re.search(r'(?m)^'+re.escape(m[1])+r'"[A-Za-z][A-Za-z0-9_]*"\s*:',text[m.end():])
            if not nxt: continue
            stop=m.end()+nxt.start()
            suffix=text[m.end():stop].rstrip()
            if not suffix.endswith(','): continue
            suffix=suffix[:-1].rstrip()
            if not suffix.endswith('"'): continue
            prose=suffix[:-1]
            try: json.loads('"'+prose+'"',strict=False);continue
            except json.JSONDecodeError: pass
            prose=prose.replace('\\r\\n','\n').replace('\\n','\n').replace('\\"','"')
            text=text[:m.end()-1]+json.dumps(prose,ensure_ascii=False)+',\n'+text[stop:]
            warnings.append('meta.json 的 '+m[2]+' 含非标准引号，已兼容读取')
        try: value=json.loads(text,strict=False)
        except json.JSONDecodeError as e:
            # SimpleJSON also accepts a missing closing object at EOF. Only accept
            # otherwise complete JSON with balanced strings and at most two closers.
            stack=[];quoted=False;escape=False;valid=True
            for ch in text:
                if quoted:
                    if escape: escape=False
                    elif ch=='\\': escape=True
                    elif ch=='"': quoted=False
                elif ch=='"': quoted=True
                elif ch in '{[': stack.append('}' if ch=='{' else ']')
                elif ch in '}]':
                    if not stack or ch!=stack.pop(): valid=False;break
            if not valid or quoted or not 1<=len(stack)<=2 or e.pos<len(text.rstrip())-1: raise
            text+=''.join(reversed(stack))
            value=json.loads(text,strict=False)
            warnings.append('meta.json 末尾缺少闭合括号，已兼容读取')
    if not isinstance(value,dict): raise ValueError('meta.json 格式无效')
    return (value,warnings,text) if return_text else (value,warnings)


def representative_names(names,limit=48):
    groups=collections.defaultdict(list)
    for name in names:
        low=name.lower()
        if low=='meta.json': continue
        if low.startswith('saves/scene/') and low.endswith('.json'): kind='scene'
        elif '/appearance/' in low and low.endswith('.vap'): kind='appearance'
        elif low.endswith('.vam') and '/clothing/' in low: kind='clothing'
        elif low.endswith('.vam') and '/hair/' in low: kind='hair'
        elif low.endswith(('.vmd','.bvh')) or '/pose/' in low and low.endswith('.vap'): kind='pose'
        elif low.endswith(('.cslist','.cs')): kind='script'
        elif low.endswith(('.assetbundle','.scene','.bundle')): kind='asset'
        elif low.endswith('.vmi'): kind='morph'
        elif '/textures/' in low and low.endswith(('.jpg','.png')): kind='texture'
        else: continue
        groups[kind].append(name)
    result=[]
    for offset in range(12):
        for rows in groups.values():
            if offset<len(rows): result.append(rows[offset])
            if len(result)>=limit: return result
    return result


def content_evidence(path):
    """Read a small selection of scene/preset contents only for requested AI jobs."""
    with zipfile.ZipFile(path) as z:
        infos=z.infolist();names=[i.filename.replace('\\','/') for i in infos]
        samples=representative_names(names)
        info=next((i for i in infos if i.filename.casefold()=='meta.json'),None)
        if info is None or info.file_size>8*1024*1024:raise ValueError('meta.json 缺失或过大')
        meta,warnings=load_metadata(z.read(info))
        result={'description':str(meta.get('description',''))[:4000],
                'instructions':str(meta.get('instructions',''))[:1400],
                'samples':samples,'scene_hints':[]}
        candidates=[i for i in infos if i.file_size<=2*1024*1024 and
            (i.filename.lower().startswith('saves/scene/') and i.filename.lower().endswith('.json')
             or '/appearance/' in i.filename.lower() and i.filename.lower().endswith('.vap'))][:3]
        for info in candidates:
            try: data=json.loads(z.read(info).decode('utf-8-sig'),strict=False)
            except (ValueError,UnicodeError): continue
            if not isinstance(data,dict): continue
            atoms=data.get('atoms',[])
            if not isinstance(atoms,list): continue
            kinds=collections.Counter(str(a.get('type','')) for a in atoms if isinstance(a,dict))
            # Types and titles describe environments/activity without transmitting poses
            # or the thousands of morph values in an appearance preset.
            result['scene_hints'].append({'title':PurePosixPath(info.filename).stem,
                'atom_types':dict(kinds),'named_objects':[str(a.get('id',''))[:100] for a in atoms
                    if isinstance(a,dict) and a.get('type') in ('CustomUnityAsset','AudioSource','AnimationPattern')][:10]})
        return result
