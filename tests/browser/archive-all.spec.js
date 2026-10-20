const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn,spawnSync}=require('child_process');

test('one-click archive spans pages and filters, respects favorites, preserves selection, and supports undo',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-archive-all-'));
  const home=path.join(root,'tool'),game=path.join(root,'game');
  fs.mkdirSync(home);fs.mkdirSync(game);fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const setup=spawnSync('python3',['-c',`
import sys
from pathlib import Path
from vampm.server import Application
from vampm.util import Job
from test_manager import var_bytes
app=Application(sys.argv[1],sys.argv[2])
app.ops.apply(app.ops.plan('restore_all',[],Job('mode'))['id'],Job('mode'))
folder=app.c.roots['active']/'original';folder.mkdir()
for i in range(50):
    name=f'Archive.Item{i:02}.1'
    (folder/(name+'.var')).write_bytes(var_bytes(name))
app.c.scan(Job('scan'))
for i in range(44):app.c.label(f'archive.item{i:02}.1',favorite=True)
app.ai.close();app.agent.close()
`,game,home],{encoding:'utf8',env:{...process.env,PYTHONPATH:[path.resolve('src'),path.resolve('tests')].join(path.delimiter)}});
  expect(setup.status,setup.stderr).toBe(0);
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
  const context=await browser.newContext(),page=await context.newPage(),errors=[],plans=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().endsWith('/api/plan')&&r.method()==='POST')plans.push(r.postDataJSON());});
  try{
    const file=path.join(home,'data/server.json');
    await expect.poll(()=>fs.existsSync(file),{timeout:10000}).toBe(true);
    const {url,token}=JSON.parse(fs.readFileSync(file,'utf8')),headers={'X-Session-Token':token};
    const get=async route=>(await page.request.get(url+'/api/'+route,{headers})).json();
    await page.goto(url+'/#'+token);await page.locator('#navigation [data-page=library]').click();
    await page.getByLabel('搜索资源').fill('Item49');await expect(page.locator('.package-row')).toHaveCount(1);
    await page.getByLabel('选择 Item49',{exact:true}).check();
    await page.locator('#navigation [data-action=favorites]').click();
    await expect(page.locator('.package-row')).toHaveCount(42);
    await page.getByLabel('搜索资源').fill('Item00');await expect(page.locator('.package-row')).toHaveCount(1);
    await page.getByRole('button',{name:'一键归档',exact:true}).click();
    await expect(page.locator('#dialog')).toContainText('全部收藏 · 44 个包');
    expect(plans.at(-1)).toEqual({kind:'organize',scope:'favorites'});
    expect(await page.evaluate(()=>[...state.selected])).toEqual(['archive.item49.1']);
    const first=path.join(game,'AddonPackages','original','Archive.Item00.1.var');
    const other=path.join(game,'AddonPackages','original','Archive.Item49.1.var');
    expect(fs.existsSync(first)).toBe(true);expect(fs.existsSync(other)).toBe(true);
    const applied=page.waitForResponse(r=>r.url().endsWith('/api/apply')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'确认执行',exact:true}).click();
    const applyJob=await (await applied).json();
    await expect.poll(async()=>(await get('job?id='+applyJob.id)).status,{timeout:15000}).toBe('done');
    await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
    expect(fs.existsSync(first)).toBe(false);expect(fs.existsSync(other)).toBe(true);
    const history=await get('history'),archive=history.find(p=>p.kind==='organize');
    const undone=await (await page.request.post(url+'/api/undo',{headers,data:{id:archive.id}})).json();
    await expect.poll(async()=>(await get('job?id='+undone.id)).status).toBe('done');
    expect(fs.existsSync(first)).toBe(true);
    await page.locator('#navigation [data-page=library]').click();
    await expect(page.getByRole('heading',{name:'资源库',exact:true})).toBeVisible();
    await page.getByLabel('搜索资源').fill('Item00');await expect(page.locator('.package-row')).toHaveCount(1);
    await page.getByRole('button',{name:'一键归档',exact:true}).click();
    await expect(page.locator('#dialog')).toContainText('全部资源 · 50 个包');
    expect(plans.at(-1)).toEqual({kind:'organize',scope:'all'});
    expect(await page.evaluate(()=>[...state.selected])).toEqual(['archive.item49.1']);
    await page.screenshot({path:'test-results/one-click-archive-preview.png',fullPage:true});
    await page.getByRole('button',{name:'取消',exact:true}).click();
    expect(fs.existsSync(first)).toBe(true);expect(fs.existsSync(other)).toBe(true);
    await page.setViewportSize({width:420,height:900});
    await expect(page.getByRole('button',{name:'一键归档',exact:true})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/one-click-archive-mobile.png',fullPage:true});
    expect(errors).toEqual([]);
  }finally{
    await context.close().catch(()=>{});
    if(proc.exitCode===null)proc.kill();
    await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
