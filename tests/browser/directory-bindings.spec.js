const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn,spawnSync}=require('child_process');

test('removed directories retain data, restore their settings, and bind moved games',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-bindings-'));
  const home=path.join(root,'tool'),game=path.join(root,'原版'),moved=path.join(root,'搬家后'),again=path.join(root,'再次搬家');
  fs.mkdirSync(home);fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const seed=spawnSync('python3',['-c',`
import sys
from pathlib import Path
from test_manager import var_bytes
from vampm.catalog import Catalog
from vampm.operations import Operations
from vampm.library_paths import change
from vampm.util import Job
game,home=map(Path,sys.argv[1:]);game.mkdir()
c=Catalog(game,home);change(c,'待用资源')
c.roots['vault'].mkdir()
(c.roots['vault']/'A.Keep.1.var').write_bytes(var_bytes('A.Keep.1'))
c.scan(Job('fixture'));c.label('a.keep.1',summary='搬家也要保留的简介',tags=['原标签'],favorite=True)
Operations(c).save_profile('保留方案',['a.keep.1'])
`,game,home],{env:{...process.env,PYTHONPATH:'src:tests'},encoding:'utf8'});
  expect(seed.status,seed.stderr).toBe(0);
  const contents=fs.readFileSync(path.join(game,'待用资源','A.Keep.1.var'));
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
  const context=await browser.newContext(),errors=[];
  try{
    const serverFile=path.join(home,'data/server.json');
    await expect.poll(()=>fs.existsSync(serverFile),{timeout:10000}).toBe(true);
    const {url,token}=JSON.parse(fs.readFileSync(serverFile,'utf8'));
    const first=await context.newPage(),second=await context.newPage(),headers={'X-Session-Token':token};
    for(const page of [first,second]){
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/api/ai-models?*',route=>route.fulfill({json:{models:[],cached:true}}));
      await page.goto(url+'/#'+token);
      await expect(page.locator('#workspace-name')).toHaveText('原版');
    }
    const status=()=>first.request.get(url+'/api/status',{headers}).then(r=>r.json());
    const saved=await status();
    const assertData=async()=>{
      const detail=await (await first.request.get(url+'/api/package?key=a.keep.1',{headers})).json();
      expect(detail.summary).toBe('搬家也要保留的简介');expect(detail.tags).toEqual(['原标签']);expect(detail.favorite).toBe(true);
      const profiles=await (await first.request.get(url+'/api/profiles',{headers})).json();
      expect(profiles.some(p=>p.name==='保留方案')).toBe(true);
    };
    const settings=()=>first.locator('.nav-item[data-page=settings]').click();
    const remove=async()=>{
      await settings();await first.locator('[data-workspace-remove=original]').click();
      await expect(first.locator('#directory-remove-form')).toContainText('数据全部保留');
      await first.getByRole('button',{name:'移除并保留数据',exact:true}).click();
      await expect(first.locator('#workspace-name')).toHaveText('未选择游戏目录');
    };
    await remove();
    await expect(second.locator('#workspace-name')).toHaveText('未选择游戏目录',{timeout:10000});
    expect((await status()).stats.files).toBe(0);
    const stale=await first.request.post(url+'/api/scan',{headers:{...headers,'X-Workspace-Epoch':saved.workspace.epoch},data:{}});
    expect(stale.status()).toBe(409);
    await settings();
    await first.getByText('已移除目录的数据（1）',{exact:true}).click();
    await expect(first.locator('.directory-archived')).toContainText(path.join(home,'data'));
    await first.screenshot({path:'test-results/游戏目录-移除保留数据.png',fullPage:true});
    await first.locator('#directory-add-form [name=path]').fill(game);
    await first.getByRole('button',{name:'＋ 保存目录',exact:true}).click();
    await expect(first.locator('.directory-row')).toHaveCount(1);
    await first.locator('[data-workspace-switch=original]').click();
    await expect(first.locator('#workspace-name')).toHaveText('原版');await assertData();
    expect((await status()).workspace.disabled_folder).toBe('待用资源');

    fs.renameSync(game,moved);
    await settings();await first.locator('[data-workspace-rebind=original]').click();
    await first.locator('#directory-rebind-form [name=path]').fill(moved);
    await first.getByRole('button',{name:'保存绑定',exact:true}).click();
    await expect(first.locator('.home-card')).toHaveCount(10);
    await expect.poll(async()=>(await status()).workspace.path).toBe(moved);
    await assertData();
    expect(fs.existsSync(game)).toBe(false);
    expect(fs.readFileSync(path.join(moved,'待用资源','A.Keep.1.var'))).toEqual(contents);

    await remove();fs.renameSync(moved,again);
    await settings();await first.getByText('已移除目录的数据（1）',{exact:true}).click();
    await first.getByRole('button',{name:'恢复／重新绑定',exact:true}).click();
    await first.locator('#directory-rebind-form [name=path]').fill(again);
    await first.getByRole('button',{name:'保存绑定',exact:true}).click();
    await expect(first.locator('.directory-row')).toHaveCount(1);
    await first.locator('[data-workspace-switch=original]').click();
    await expect(first.locator('#workspace-name')).toHaveText('原版');await assertData();
    expect(fs.readFileSync(path.join(again,'待用资源','A.Keep.1.var'))).toEqual(contents);
    await settings();
    await first.screenshot({path:'test-results/游戏目录-重新绑定.png',fullPage:true});
    await first.setViewportSize({width:420,height:900});
    await first.screenshot({path:'test-results/游戏目录-重新绑定-窄屏.png',fullPage:true});
    expect(await first.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect((await status()).game_directories.archived).toEqual([]);
    const registry=JSON.parse(fs.readFileSync(path.join(home,'data/game-directories.json'),'utf8'));
    expect(registry.items).toHaveLength(1);expect(registry.current).toBe('original');expect(registry.items[0].path).toBe(again);
    expect(errors).toEqual([]);
  }finally{
    await context.close().catch(()=>{});
    if(proc.exitCode===null)proc.kill();
    await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
