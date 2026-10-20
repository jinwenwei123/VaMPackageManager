const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn}=require('child_process');

test('saved game directories switch all tabs and preserve separate layouts and settings',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-directories-'));
  const home=path.join(root,'tool'),game=path.join(root,'原版'),other=path.join(root,'另一版本');
  fs.mkdirSync(home);fs.mkdirSync(path.join(game,'AddonPackages'),{recursive:true});
  fs.mkdirSync(path.join(other,'AddonPackages'),{recursive:true});
  fs.mkdirSync(path.join(other,'备用资源'));
  fs.writeFileSync(path.join(other,'备用资源','说明.txt'),'保留原内容');
  fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
  const context=await browser.newContext(),errors=[];
  try{
    const file=path.join(home,'data/server.json');
    await expect.poll(()=>fs.existsSync(file),{timeout:10000}).toBe(true);
    const {url,token}=JSON.parse(fs.readFileSync(file,'utf8'));
    const first=await context.newPage(),second=await context.newPage();
    for(const page of [first,second]){
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/api/ai-models?*',route=>route.fulfill({json:{models:[],cached:true}}));
      await page.goto(url+'/#'+token);
      await expect(page.locator('#workspace-name')).toHaveText('原版');
    }
    const status=await (await first.request.get(url+'/api/status',{headers:{'X-Session-Token':token}})).json();
    const oldHeaders={'X-Session-Token':token,'X-Workspace-Epoch':status.workspace.epoch};
    await first.request.post(url+'/api/home-layout',{headers:oldHeaders,data:{cards:['directories','library']}});
    await first.request.post(url+'/api/appearance',{headers:oldHeaders,data:{palette:'violet',mode:'dark'}});
    await first.locator('[data-home-card=directories] .home-arrow').click();
    await first.locator('#directory-add-form [name=name]').fill('测试版本');
    await first.locator('#directory-add-form [name=path]').fill(other);
    await expect(first.locator('#directory-add-form [name=disabled_folder]')).toHaveValue('.DisabledPackages');
    await expect(first.locator('#directory-add-form input[disabled]')).toHaveValue('AddonPackages');
    await first.locator('#directory-add-form [name=disabled_folder]').fill('备用资源');
    await first.getByRole('button',{name:'＋ 保存目录',exact:true}).click();
    await expect(first.locator('.directory-row')).toHaveCount(2);
    await first.locator('.directory-row').filter({hasText:'测试版本'}).getByRole('button',{name:'改名',exact:true}).click();
    await first.locator('#directory-rename-form [name=name]').fill('另一版本');
    await first.getByRole('button',{name:'保存名称',exact:true}).click();
    await first.screenshot({path:'test-results/游戏目录-列表.png',fullPage:true});
    await first.locator('.directory-row').filter({hasText:'另一版本'}).getByRole('button',{name:'切换',exact:true}).click();
    await expect(first.locator('#workspace-name')).toHaveText('另一版本');
    await expect(second.locator('#workspace-name')).toHaveText('另一版本',{timeout:10000});
    await expect(first.locator('.home-card')).toHaveCount(10);
    const stale=await first.request.post(url+'/api/appearance',{headers:oldHeaders,data:{palette:'ocean',mode:'light'}});
    expect(stale.status()).toBe(409);expect((await stale.json()).workspace_changed).toBe(true);
    const staleUpload=await first.request.post(url+'/api/upload?name=wrong.var',{headers:oldHeaders,data:Buffer.from('stale')});
    expect(staleUpload.status()).toBe(409);
    await first.locator('.nav-item[data-page=settings]').click();
    await expect(first.locator('#directory-folders-form [name=disabled_folder]')).toHaveValue('备用资源');
    await expect(first.locator('#directory-folders-form input[disabled]')).toHaveValue('AddonPackages');
    await first.locator('#directory-folders-form [name=disabled_folder]').fill('我的停用库');
    await first.getByRole('button',{name:'保存资源文件夹',exact:true}).click();
    await expect(first.locator('.home-card')).toHaveCount(10); // Saving reloads all pages.
    expect(fs.readFileSync(path.join(other,'我的停用库','说明.txt'),'utf8')).toBe('保留原内容');
    expect(fs.existsSync(path.join(other,'备用资源'))).toBe(false);
    await first.locator('.nav-item[data-page=settings]').click();
    await expect(first.locator('#directory-folders-form [name=disabled_folder]')).toHaveValue('我的停用库');
    await first.screenshot({path:'test-results/游戏目录-资源文件夹.png',fullPage:true});
    await first.setViewportSize({width:420,height:900});
    await first.screenshot({path:'test-results/游戏目录-资源文件夹-窄屏.png',fullPage:true});
    expect(await first.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await first.setViewportSize({width:1440,height:1000});
    await first.locator('.nav-item[data-page=home]').click();
    await first.locator('[data-home-card=appearance] [data-palette-choice=ocean]').click();
    await first.locator('[data-workspace-select]').selectOption('original');
    await expect(first.locator('#workspace-name')).toHaveText('原版');
    await expect(first.locator('.home-card')).toHaveCount(2);
    await expect(second.locator('#workspace-name')).toHaveText('原版',{timeout:10000});
    const restored=await (await first.request.get(url+'/api/status',{headers:{'X-Session-Token':token}})).json();
    expect(restored.appearance).toEqual({palette:'violet',mode:'dark'});
    expect(restored.game_directories.items.find(p=>p.id==='original').disabled_folder).toBe('.DisabledPackages');
    expect(restored.game_directories.items.find(p=>p.id!=='original').disabled_folder).toBe('我的停用库');
    expect(fs.existsSync(path.join(home,'data/catalog.sqlite3'))).toBe(true);
    expect(fs.readdirSync(path.join(home,'data/workspaces'))).toHaveLength(1);
    expect(fs.readdirSync(path.join(game,'AddonPackages'))).toEqual([]);
    expect(fs.readdirSync(path.join(other,'AddonPackages'))).toEqual([]);
    expect(errors).toEqual([]);
  }finally{
    await context.close().catch(()=>{});
    if(proc.exitCode===null)proc.kill();
    await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
