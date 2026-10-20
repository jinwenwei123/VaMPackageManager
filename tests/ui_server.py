import json
import shutil
import sys
import tempfile
import struct
import zlib
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from test_manager import var_bytes
from vampm.server import Application,serve
from vampm.util import Job
import vampm.agent as agent_module

class FixtureCodex:
    """UI fixture uses real package tools, with deterministic text instead of paid inference."""
    def __init__(self,*args):self.thread_id=None;self.process=None;self.turns=0
    def connect_thread(self,*args):self.thread_id='ui-fixture';return self.thread_id
    def turn(self,text,cancel,emit,tool):
        import time
        self.turns+=1
        row=tool('search_packages',{'q':'Luna'})['items'][0]
        if '创建' in text:
            tool('manage_profiles',{'action':'save','name':'助手测试方案','keys':[row['key']]})
            answer='已创建助手测试方案，包含 Luna。'
        elif 'Markdown' in text:
            answer='## 推荐本地 Luna\n\n**人物包**，用于外观搭配。\n\n| 包名 | 类型 |\n|---|---|\n| Luna | 人物 |\n\n```text\n示例文本\n```\n\n[作者页面](https://example.com)\n\n<img src=x onerror="window.mdUnsafe=1"><script>window.mdUnsafe=2</script>[无效链接](javascript:alert(1))'
        else:answer='推荐本地 Luna 人物包；可以继续创建启动方案。'
        if '创建' not in text:tool('show_packages',{'keys':[row['key']],'title':'为你推荐'})
        for part in [answer[:6],answer[6:]]:
            if cancel.wait(.08):return
            emit('delta',{'id':'ui-answer-'+str(self.turns),'text':part})
    def close(self):pass

agent_module.CodexChat=FixtureCodex

project=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as td:
    root=Path(td);game=root/'game';home=root/'tool'
    game.mkdir();home.mkdir()
    shutil.copytree(project/'web',home/'web')
    addon=game/'AddonPackages';addon.mkdir()
    packages=[('Studio.GardenDance.1',['Assets.Garden.1'],{'Saves/scene/Garden.json':'{}'}),
              ('Assets.Garden.1',[],{'Custom/Assets/Garden.assetbundle':'sample'}),
              ('Artist.Luna.2',[],{'Custom/Atom/Person/Appearance/Preset_Luna.vap':'{}'}),
              ('Style.CottonDress.1',[],{'Custom/Clothing/Female/Dress.vam':'{}'}),
              ('Hair.StudioBob.3',[],{'Custom/Hair/Female/Bob.vam':'{}'}),
              ('Demo.NeedDependency.1',['Absent.Light.2'],{'Saves/scene/Light.json':'{}'})]
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',96,128,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(b'\0'+bytes((120+y//2,160+y//3,138))*96 for y in range(128))))+chunk(b'IEND',b'')
    for p,d,f in packages:
        for name in list(f):
            if name.endswith(('.json','.vap','.vam')):f[str(Path(name).with_suffix('.png'))]=png
        (addon/(p+'.var')).write_bytes(var_bytes(p,d,f))
    (addon/'duplicate').mkdir()
    shutil.copy2(addon/'Assets.Garden.1.var',addon/'duplicate/Assets.Garden.1.var')
    app=Application(game,home);app.c.scan(Job('fixture'))
    def fixture_models(provider=None,refresh=False,cancel=None,*,cfg=None,service=None):
        codex=provider=='codex'
        rows=([{'id':m,'name':m,'vision':True,'tags':['识图','推理'],'hidden':False}
               for m in ['gpt-6.1-sol','gpt-6-luna','gpt-6-astra','gpt-5.6-sol']]+
              [{'id':'hidden-model','name':'hidden-model','vision':True,'tags':['识图','CLI 隐藏 / 专用'],'hidden':True}]) if codex else [
               {'id':'deepseek-flash','name':'DeepSeek Flash','vision':True,'tags':['识图','推理']},
               {'id':'deepseek-v4-pro','name':'DeepSeek Pro','vision':False,'tags':['仅文字','推理']}]
        for row in rows:
            row['reasoning_efforts']=(['low','medium','high','xhigh','max'] if codex else ['low','high','max']) if not row.get('hidden') else []
            row['default_reasoning_effort']=('medium' if codex else 'high') if row['reasoning_efforts'] else None
        return {'models':rows,'cached':False,'provider':'codex' if codex else 'api'}
    app.ai.models.get=fixture_models
    def fixture_summaries(items,job,target="summary"):
        import time
        for _ in range(12):
            job.check();time.sleep(.1)
        return {p['key']:{**({'category':'人物','summary':p['name']+' 人物外观，提供可搭配的人物形象。'} if target!='tags' else {}),
                          **({'tags':['女性形象','银白发','长发','幻想风','精灵耳','淡妆']} if target!='summary' else {})} for p in items}
    app.ai.classify_batch=fixture_summaries
    def fixture_prompt(cfg,instruction,items,schema,job,model,images=(),**options):
        import time
        for _ in range(10):
            job.check();time.sleep(.1)
        if isinstance(items,dict) and 'text' in items:
            return {'can_repair':True,'reason':'删除 JSON 末尾逗号，保留原始字段。','edits':[{'before':',}', 'after':'}'}]}
        if isinstance(items,list) and items and 'id' in items[0]:
            return {'items':[{'id':x['id'],'filename':x['creatorName']+'.'+x['packageName']+'.2.var',
                             'reason':'根据包内作者、包名和原版本2，移除下载重名后缀。','confidence':'high'} for x in items]}
        if items['title']=='生成失败':raise ValueError('模拟接口错误，原提示词保留。')
        return {'prompt':'请从本地资源库中'+items['title']+'，结合已有简介说明理由，并展示推荐卡片。'}
    app.ai.complete_json=fixture_prompt
    with zipfile.ZipFile(home/'web-json-fixture.var','w') as z:
        z.writestr('meta.json','{"creatorName":"Repair","packageName":"Json","dependencies":{},}')
        z.writestr('Custom/Hair/Female/Example/Hair.vam','{}')
    results=project/'test-results';results.mkdir(exist_ok=True)
    shutil.copy2(home/'web-json-fixture.var',results/'Repair.Json.1.var')
    luna=app.c.packages()['artist.luna.2']['files'][0]
    with app.c.db() as db:
        db.execute('INSERT INTO file_warnings VALUES(?,?,?,?)',(luna['id'],luna['size'],luna['mtime'],
            json.dumps(['文件大小与发布信息不符；压缩包完整性与身份校验通过，文件已保留。'])))
    app.c.invalidate()
    server=serve(app,47851)
    (results/'ui-server.json').write_text(json.dumps({'url':'http://127.0.0.1:47851','token':app.token,
        'fixtureAddon':str(addon), 'fixtureVault':str(app.c.roots['vault'])}))
    (results/'Multi.Bundle.1.var').write_bytes(var_bytes('Multi.Bundle.1',deps=['Assets.Garden.1'],files={'Saves/scene/Demo.json':'{}','Custom/Atom/Person/Appearance/Look.vap':'{}','Custom/Atom/Person/Clothing/Outfit.vap':'{}'}))
    (results/'Repair.Look.2.var').write_bytes(var_bytes('Repair.Look.2'))
    (results/'CopyVariantA.var').write_bytes(var_bytes('Copies.Scene.1', deps=['Copies.Hair.1']))
    (results/'CopyVariantB.var').write_bytes(var_bytes('Copies.Scene.1', deps=['Copies.Outfit.1']))
    for package in ['Copies.Hair.1', 'Copies.Outfit.1']:
        (results/(package+'.var')).write_bytes(var_bytes(package))
    (results/'Import.NewHair.1.var').write_bytes(var_bytes('Import.NewHair.1',files={'Custom/Hair/Female/New.vam':'{}'}))
    folder=results/'文件夹导入测试';(folder/'子目录').mkdir(parents=True,exist_ok=True)
    (folder/'子目录/Folder.NewLook.1.var').write_bytes(var_bytes('Folder.NewLook.1',deps=['Assets.Garden.1','Absent.Light.2'],
        files={'Custom/Atom/Person/Appearance/Look.vap':'{}','Custom/Atom/Person/Appearance/Look.png':png}))
    (folder/'封面.PNG').write_bytes(png)
    (folder/'子目录/依赖.txt').write_text('保留原说明文档',encoding='utf-8')
    (folder/'参数.json').write_text('{}',encoding='utf-8')
    (folder/'空白.txt').write_bytes(b'')
    (folder/'忽略.exe').write_bytes(b'unsupported fixture, never executed')
    for i in range(44):
        package=f'Scope.Item{i:02d}.1'
        files={'Custom/Atom/Person/Appearance/Look.vap':'{}'} if i<42 else {'Saves/scene/Test.json':'{}'}
        (results/(package+'.var')).write_bytes(var_bytes(package,files=files))
    for i in range(24):
        package=f'AIQueue.Look{i:02d}.1'
        (results/(package+'.var')).write_bytes(var_bytes(package))
    try:server.serve_forever()
    finally:server.server_close()
