const manual=require('./manual_windows');
/* Native EXE and real-library PREVIEW only. Never applies moves or saves choices. */
const {chromium,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),crypto=require('crypto'),{execFileSync}=require('child_process');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const digest=file=>fs.existsSync(file)?crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'):null;
async function exited(server){
  const start=Date.now();
  for(let i=0;i<180;i++){
    let alive=true;try{process.kill(server.pid,0);}catch{alive=false;}
    if(!alive&&!fs.existsSync('data/server.json'))return Date.now()-start;
    await pause(100);
  }
  throw Error('Package manager did not exit');
}
(async()=>{
  if(fs.existsSync('data/server.json'))throw Error('Close existing manager pages before running this test');
  const protectedFiles=['data/ai-settings.json','data/profiles.json','data/agent/conversation.json',
    'data/library-preferences.json','data/appearance.json','data/activation-choices.json'];
  const before=Object.fromEntries(protectedFiles.map(file=>[file,digest(file)]));
  let browser,server;const report={errors:[]};
  try{
    execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
    for(let i=0;i<100&&!fs.existsSync('data/server.json');i++)await pause(100);
    server=JSON.parse(fs.readFileSync('data/server.json','utf8'));
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',e=>report.errors.push(e.message));
    const get=async route=>(await page.request.get(server.url+'/api/'+route,{headers:{'X-Session-Token':server.token}})).json();
    await page.goto(server.url+'/#'+server.token);
    const status=await get('status');expect(status.version).toBe('1.10.0');report.version=status.version;
    report.packages=status.stats.packages;report.files=status.stats.files;
    const originalMode=(await get('profiles')).find(p=>p.name==='基础收藏').duplicate_mode;
    const started=Date.now();
    await page.getByRole('button',{name:'▷ 启动方案',exact:true}).click();
    const card=page.locator('.profile').filter({hasText:'基础收藏'});
    await expect(card).toBeVisible({timeout:60000});report.profiles_ms=Date.now()-started;
    await card.getByRole('button',{name:'预览启用',exact:true}).click();
    const dialog=page.locator('#dialog');
    await expect(dialog).toBeVisible({timeout:60000});
    report.conflict_groups=await dialog.locator('.copy-group').count();
    expect(report.conflict_groups).toBeGreaterThan(0);
    const initial=(await get('job?id='+(await get('status')).jobs[0].id)).result;
    if(initial.unresolved_copies.length)await expect(dialog.getByRole('button',{name:'确认执行'})).toBeDisabled();
    else await expect(dialog.getByRole('button',{name:'确认执行'})).toBeEnabled();
    await page.screenshot({path:'test-results/Windows-1.10-副本选择.png'});
    await dialog.locator('#remember-copies').uncheck();
    await dialog.locator('[name=copy-mode][value=all]').check();
    await dialog.getByRole('button',{name:'更新预览'}).click();
    await expect(dialog).toBeVisible({timeout:60000});
    await expect(dialog.locator('[name=copy-mode][value=all]')).toBeChecked({timeout:60000});
    await expect(dialog.locator('#remember-copies')).not.toBeChecked();
    await expect(dialog).toContainText('只采用一份');
    const allJob=(await get('status')).jobs[0];
    // Read the most recently completed preview through the public jobs endpoint.
    expect(allJob.status).toBe('done');
    {
      const p=(await get('job?id='+allJob.id)).result;
      report.all={included:p.included,files:p.included_files,missing:p.missing.length,moves:p.count,unresolved:p.unresolved_copies};
      expect(p.unresolved_copies).toEqual([]);
    }
    await page.screenshot({path:'test-results/Windows-1.10-全部保留.png'});
    await dialog.locator('[name=copy-mode][value=select]').check();
    const selectors=dialog.locator('[data-copy-key]');
    for(let i=0;i<await selectors.count();i++){
      const el=selectors.nth(i);
      const option=await el.locator('option').evaluateAll(options=>options.find(o=>o.value&&!o.disabled).value);
      await el.selectOption(option);
    }
    await dialog.getByRole('button',{name:'更新预览'}).click();
    await expect(dialog).toBeVisible({timeout:60000});
    await expect(dialog.locator('[name=copy-mode][value=select]')).toBeChecked({timeout:60000});
    await expect(dialog.locator('#remember-copies')).not.toBeChecked();
    await expect(dialog.locator('#copy-preview-status')).not.toContainText('请先完成');
    const selectedJob=(await get('status')).jobs[0];
    expect(selectedJob.status).toBe('done');
    {
      const p=(await get('job?id='+selectedJob.id)).result;
      report.select={included:p.included,files:p.included_files,missing:p.missing.length,moves:p.count,unresolved:p.unresolved_copies};
      expect(p.unresolved_copies).toEqual([]);expect(p.included_files).toBe(p.included);
      expect(p.missing.length).toBeGreaterThan(0);
      await expect(dialog).toContainText('仍可启用已有可用包');
      await expect(dialog.getByRole('button',{name:'确认执行'})).toBeEnabled();
    }
    await page.setViewportSize({width:390,height:800});
    await dialog.locator('.copy-details summary').first().click();
    report.narrow_overflow=await dialog.evaluate(el=>el.scrollWidth>el.clientWidth+1);
    expect(report.narrow_overflow).toBe(false);
    await page.screenshot({path:'test-results/Windows-1.10-副本窄屏.png'});
    await dialog.getByRole('button',{name:'取消',exact:true}).click();
    expect((await get('profiles')).find(p=>p.name==='基础收藏').duplicate_mode).toBe(originalMode);
    await page.getByRole('button',{name:/⊞资源库|⊞ 资源库/}).click();
    await page.getByRole('button',{name:'＋ 添加资源'}).click();
    await expect(page.locator('#import-folder')).toBeVisible();
    report.import_options=await get('import-options');
    await page.screenshot({path:'test-results/Windows-1.10-文件夹导入.png'});
    await dialog.getByRole('button',{name:'取消',exact:true}).click();
    expect(report.errors).toEqual([]);
    await browser.close();browser=null;report.exit_ms=await exited(server);
    report.protected_files_unchanged=Object.fromEntries(protectedFiles.map(file=>[file,digest(file)===before[file]]));
    expect(Object.values(report.protected_files_unchanged).every(Boolean)).toBe(true);
    fs.writeFileSync('test-results/windows-activation.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  }finally{
    if(browser)await browser.close();if(server)await exited(server);
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
