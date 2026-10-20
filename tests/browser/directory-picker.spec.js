const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn}=require('child_process');

test('folder selection fills the root name, duplicates block saving, and fixed folders are disabled',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-picker-'));
  const home=path.join(root,'tool'),game=path.join(root,'VaM'),other=path.join(root,'other','VaM'),moved=path.join(root,'预览版');
  for(const dir of [game,other,moved])fs.mkdirSync(path.join(dir,'AddonPackages'),{recursive:true});
  fs.mkdirSync(home);fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
  const context=await browser.newContext(),errors=[];
  try{
    const file=path.join(home,'data/server.json');
    await expect.poll(()=>fs.existsSync(file),{timeout:10000}).toBe(true);
    const {url,token}=JSON.parse(fs.readFileSync(file,'utf8')),page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/ai-models?*',r=>r.fulfill({json:{models:[],cached:true}}));
    let chosen=other,pickerCalls=0;
    await page.route('**/api/game-directories/browse',r=>{
      pickerCalls++;return r.fulfill({json:chosen?{path:chosen,name:path.basename(chosen),cancelled:false}:{cancelled:true}});
    });
    await page.goto(url+'/#'+token);
    await page.locator('.nav-item[data-page=settings]').click();
    const form=page.locator('#directory-add-form'),name=form.locator('[name=name]'),input=form.locator('[name=path]');
    const fixed=form.locator('.directory-fixed-folder');
    await expect(fixed).toHaveValue('AddonPackages');await expect(fixed).toBeDisabled();
    expect(await fixed.evaluate(e=>{e.focus();return document.activeElement===e;})).toBe(false);
    expect(await fixed.evaluate(e=>getComputedStyle(e).userSelect)).toBe('none');
    await expect(page.locator('#directory-folders-form .directory-fixed-folder')).toBeDisabled();
    await form.getByRole('button',{name:'选择文件夹',exact:true}).click();
    await expect(input).toHaveValue(other);await expect(name).toHaveValue('VaM');
    await expect(form.locator('[data-directory-name-error]')).toContainText('名称已存在');
    await expect(form.getByRole('button',{name:'＋ 保存目录',exact:true})).toBeDisabled();
    await page.screenshot({path:'test-results/游戏目录-选择文件夹与重名提示.png',fullPage:true});
    chosen=null;
    await form.getByRole('button',{name:'选择文件夹',exact:true}).click();
    await expect(form.getByRole('button',{name:'选择文件夹',exact:true})).toBeEnabled();
    await expect(input).toHaveValue(other);await expect(name).toHaveValue('VaM');
    await name.fill('VaM 测试版');
    await expect(form.locator('[data-directory-name-error]')).toBeHidden();
    await form.getByRole('button',{name:'＋ 保存目录',exact:true}).click();
    await expect(page.locator('.directory-row')).toHaveCount(2);
    await input.fill('C:\\Games\\Alpha\\');await expect(name).toHaveValue('Alpha');
    chosen=moved;
    await form.getByRole('button',{name:'选择文件夹',exact:true}).click();
    await expect(name).toHaveValue('预览版');
    const row=page.locator('.directory-row').filter({hasText:'VaM 测试版'});
    await row.getByRole('button',{name:'更改位置',exact:true}).click();
    const rebind=page.locator('#directory-rebind-form');
    await rebind.getByRole('button',{name:'选择文件夹',exact:true}).click();
    await expect(rebind.locator('[name=path]')).toHaveValue(moved);
    await expect(rebind.locator('[name=name]')).toHaveValue('VaM 测试版');
    await rebind.locator('[name=name]').fill('VaM');
    await expect(rebind.getByRole('button',{name:'保存绑定',exact:true})).toBeDisabled();
    await rebind.locator('[name=name]').fill('搬家后的测试版');
    await rebind.getByRole('button',{name:'保存绑定',exact:true}).click();
    await expect(page.locator('.directory-row').filter({hasText:'搬家后的测试版'})).toContainText(moved);
    await page.setViewportSize({width:420,height:900});
    await page.screenshot({path:'test-results/游戏目录-文件夹选择-窄屏.png',fullPage:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect(pickerCalls).toBe(4);expect(errors).toEqual([]);
  }finally{
    await context.close().catch(()=>{});
    if(proc.exitCode===null)proc.kill();
    await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
