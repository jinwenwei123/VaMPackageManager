const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn,spawnSync}=require('child_process');

test('legacy staging migrates in settings without losing drafts and Chinese vault can use English name',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-storage-'));
  const home=path.join(root,'tool'),game=path.join(root,'game'),legacy=path.join(home,'data','quarantine');
  fs.mkdirSync(path.join(game,'AddonPackages'),{recursive:true});
  fs.mkdirSync(path.join(game,'.未启用模组'));fs.writeFileSync(path.join(game,'.未启用模组','keep.txt'),'inactive fixture');
  fs.mkdirSync(legacy,{recursive:true});fs.writeFileSync(path.join(legacy,'keep.txt'),'quarantine fixture');
  fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const setup=spawnSync('python3',['-c',"import sys;from vampm.catalog import Catalog;Catalog(sys.argv[1],sys.argv[2],disabled_folder='.未启用模组')",game,home],{env:{...process.env,PYTHONPATH:path.resolve('src')},encoding:'utf8'});
  expect(setup.status,setup.stderr).toBe(0);
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
  const context=await browser.newContext(),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  try{
    const file=path.join(home,'data/server.json');
    await expect.poll(()=>fs.existsSync(file),{timeout:10000}).toBe(true);
    const {url,token}=JSON.parse(fs.readFileSync(file,'utf8'));
    await page.route('**/api/ai-models?*',route=>route.fulfill({json:{models:[],cached:true}}));
    await page.goto(url+'/#'+token);await page.locator('.nav-item[data-page=settings]').click();
    await expect(page.locator('#game-storage-panel')).toContainText('旧位置');
    await expect(page.locator('#directory-folders-form [name=disabled_folder]')).toHaveValue('.未启用模组');
    await page.locator('#directory-add-form [name=name]').fill('unsaved draft');
    await page.getByRole('button',{name:'迁移旧管理文件',exact:true}).click();
    await expect(page.locator('#game-storage-panel')).toContainText('暂存和隔离区已使用游戏目录内的 .PackageManagerStorage');
    await expect(page.locator('#directory-add-form [name=name]')).toHaveValue('unsaved draft');
    expect(fs.existsSync(legacy)).toBe(false);
    const status=await (await page.request.get(url+'/api/status',{headers:{'X-Session-Token':token}})).json();
    expect(status.workspace.storage.legacy).toEqual([]);
    expect(fs.readFileSync(path.join(status.workspace.storage.paths.quarantine,'keep.txt'),'utf8')).toBe('quarantine fixture');
    await page.locator('#directory-folders-form [name=disabled_folder]').fill('.DisabledPackages');
    await expect(page.locator('#directory-folders-form [name=disabled_folder]')).toHaveValue('.DisabledPackages');
    expect(fs.existsSync(path.join(game,'.未启用模组'))).toBe(true);
    await page.getByRole('button',{name:'保存资源文件夹',exact:true}).click();
    await expect(page.locator('.nav-item[data-page=home]')).toHaveClass(/active/);
    expect(fs.readFileSync(path.join(game,'.DisabledPackages','keep.txt'),'utf8')).toBe('inactive fixture');
    expect(fs.existsSync(path.join(game,'.未启用模组'))).toBe(false);
    await page.locator('.nav-item[data-page=settings]').click();
    await expect(page.locator('#directory-folders-form [name=disabled_folder]')).toHaveValue('.DisabledPackages');
    await page.screenshot({path:'test-results/game-storage-settings.png',fullPage:true});
    await page.setViewportSize({width:420,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/game-storage-settings-mobile.png',fullPage:true});
    expect(errors).toEqual([]);
  }finally{
    await context.close().catch(()=>{});
    if(proc.exitCode===null)proc.kill();
    await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
