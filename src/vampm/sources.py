"""Author release discovery and explicitly configured public download pages."""
import json
import re
import time
import urllib.parse
import uuid
from html.parser import HTMLParser

from .util import Cancelled, atomic_json, read_json, matches, split_id


def public_https(url):
    try:
        u=urllib.parse.urlsplit(url);host=u.hostname or ''
        return (u.scheme=='https' and not u.username and not u.password and u.port in (None,443)
            and '.' in host and not re.fullmatch(r'[\d.]+',host)
            and not host.endswith(('.local','.localhost','.internal','.lan')))
    except ValueError: return False


def github_repo(url):
    if not public_https(url): return None
    u=urllib.parse.urlsplit(url);parts=u.path.strip('/').split('/')
    if u.hostname=='github.com' and len(parts)>=2 and all(re.fullmatch(r'[\w.-]+',p) for p in parts[:2]):
        return '/'.join(parts[:2]).removesuffix('.git')


def filename_in(value):
    text=urllib.parse.unquote(value)
    for match in re.findall(r'[^\s/?&#"<>\\=]+\.var\b',text,re.I):
        if split_id(match): return match


class Links(HTMLParser):
    def __init__(self): super().__init__();self.links=[];self.current=None
    def handle_starttag(self,tag,attrs):
        if tag!='a': return
        attrs=dict(attrs);self.current=attrs.get('href')
        if self.current:
            name=filename_in(self.current) or filename_in(attrs.get('download','')) or filename_in(attrs.get('title',''))
            if name:self.links.append((self.current,name))
    def handle_data(self,text):
        if self.current and (name:=filename_in(text)):self.links.append((self.current,name))
    def handle_endtag(self,tag):
        if tag=='a':self.current=None


class Sources:
    def __init__(self,catalog):
        self.c=catalog;self.file=catalog.data/'download-sources.json'
        self.cachefile=catalog.data/'github-release-cache.json'

    def settings(self): return read_json(self.file,{'sites':[]})

    def save(self,data):
        sites=data.get('sites',[])
        if not isinstance(sites,list) or len(sites)>30:raise ValueError('最多添加 30 个公开来源')
        clean=[]
        for item in sites:
            if not isinstance(item,dict):raise ValueError('来源格式无效')
            name=str(item.get('name','')).strip();url=str(item.get('url','')).strip()
            prefix=str(item.get('prefix','')).strip()
            hosts=item.get('download_hosts',[])
            if isinstance(hosts,str):hosts=[x.strip().lower() for x in hosts.split(',') if x.strip()]
            if (not name or len(name)>80 or len(url)>2000 or not public_https(url)
                or not re.fullmatch(r'[^/\\\s:.*]+\.(?:\*|[^/\\\s:*]+)',prefix)):
                raise ValueError('请填写名称、作者.* 或 作者.包名，以及公开 HTTPS 页面地址')
            if not isinstance(hosts,list) or len(hosts)>10 or any(not isinstance(h,str) or
                '/' in h or ':' in h or not public_https('https://'+h) for h in hosts):
                raise ValueError('下载域名请仅填写公开网站域名，多个用逗号分开')
            uid=str(item.get('id',''))
            if not re.fullmatch('[a-f0-9]{32}',uid):uid=uuid.uuid4().hex
            clean.append({'id':uid,'name':name,'url':url,'prefix':prefix,'download_hosts':hosts})
        atomic_json(self.file,{'sites':clean});return {'sites':clean}

    def hosts(self,source_id):
        row=next((s for s in self.settings()['sites'] if s['id']==source_id),None)
        return [urllib.parse.urlsplit(row['url']).hostname]+row['download_hosts'] if row else []

    def repositories(self,defaults):
        by_family={k:[v] for k,v in defaults.items()}
        for package in self.c.packages().values():
            repo=github_repo(package.get('url',''))
            if repo:
                family=package['key'].rsplit('.',1)[0]
                if repo not in by_family.setdefault(family,[]):by_family[family].append(repo)
        for site in self.settings()['sites']:
            repo=github_repo(site['url'])
            if repo: by_family.setdefault(site['prefix'].casefold(),[]).append(repo)
        return by_family

    def releases(self,repo,job):
        from .providers import trusted_json
        cache=read_json(self.cachefile,{})
        saved=cache.get(repo.casefold(),{})
        if time.time()-saved.get('time',0)<6*3600:return saved['assets']
        assets=[]
        for page in range(1,4):
            job.check()
            rows=trusted_json(f'https://api.github.com/repos/{repo}/releases?per_page=100&page={page}')
            if not isinstance(rows,list):raise ValueError('GitHub 返回格式无法识别')
            assets.extend(a for row in rows for a in row.get('assets',[]) if a.get('name','').lower().endswith('.var'))
            if len(rows)<100:break
        cache[repo.casefold()]={'time':time.time(),'assets':assets}
        atomic_json(self.cachefile,cache)
        return assets

    @staticmethod
    def applies(prefix,ref):
        family=ref.rsplit('.',1)[0].casefold();prefix=prefix.casefold()
        return family.startswith(prefix[:-1]) if prefix.endswith('.*') else family==prefix

    def external(self,missing,job,errors):
        from .providers import trusted_open, approved_url
        candidates=[]
        for site in self.settings()['sites']:
            if github_repo(site['url']):continue
            refs=[x['ref'] for x in missing if self.applies(site['prefix'],x['ref'])]
            if not refs:continue
            job.check();job.message='检查公开来源：'+site['name']
            hosts=self.hosts(site['id']);rows=[]
            try:
                direct=filename_in(site['url'])
                if direct:rows=[{'filename':direct,'url':site['url']}]
                else:
                    with trusted_open(site['url'],extra_hosts=hosts) as response:
                        body=response.read(4*1024*1024+1)
                    if len(body)>4*1024*1024:raise ValueError('公开页面超过 4 MiB')
                    text=body.decode('utf-8-sig',errors='replace')
                    if text.lstrip().startswith(('[','{')):
                        data=json.loads(text);rows=data.get('packages',[]) if isinstance(data,dict) else data
                        if not isinstance(rows,list):raise ValueError('资源列表格式无效')
                    else:
                        parser=Links();parser.feed(text)
                        rows=[{'filename':name,'url':url} for url,name in parser.links]
                for row in rows[:5000]:
                    if not isinstance(row,dict):continue
                    name=str(row.get('filename',''));url=urllib.parse.urljoin(site['url'],str(row.get('url','')))
                    if not name.lower().endswith('.var') or not approved_url(url,hosts):continue
                    related=[r for r in refs if matches(r,name)]
                    if related:
                        candidates.append({'filename':name,'url':url,'bytes':max(0,int(row.get('bytes') or 0)),
                            'refs':related,'source':site['name'],'source_id':site['id'],'page_url':site['url']})
            except Cancelled:raise
            except Exception as e:errors.append(site['name']+'：'+str(e)[:200])
        return candidates
