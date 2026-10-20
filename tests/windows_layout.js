const manual=require('./manual_windows');
/* Native EXE with the real library: previews only, no file operations or AI calls. */
const {chromium,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),crypto=require('crypto'),{execFileSync}=require('child_process');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const digest=p=>fs.existsSync(p)?crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex'):null;
async function exited(server){
  const start=Date.now();
  for(let n=0;n<200;n++){
    let alive=true;try{process.kill(server.pid,0);}catch{alive=false;}
    if(!alive&&!fs.existsSync('data/server.json'))return Date.now()-start;
    await pause(100);
  }
  throw Error('Native server did not exit after closing the last page');
}
(async()=>{
  if(fs.existsSync('data/server.json'))throw Error('Close the manager first');
  const protectedFiles=['data/ai-settings.json','data/profiles.json','data/agent/conversation.json',
    'data/library-preferences.json','data/appearance.json','data/activation-choices.json','data/base-packages.json','data/resource-folders.json'];
  const before=Object.fromEntries(protectedFiles.map(p=>[p,digest(p)]));
  const report={errors:[]};let browser,server;
  try{
    execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
    for(let i=0;i<100&&!fs.existsSync('data/server.json');i++)await pause(100);
    server=JSON.parse(fs.readFileSync('data/server.json','utf8'));
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    const page=await browser.newPage({viewport:{width:1600,height:1000}});
    page.on('pageerror',e=>report.errors.push(e.message));
    const headers={'X-Session-Token':server.token};
    const get=async route=>(await page.request.get(server.url+'/api/'+route,{headers})).json();
    await page.goto(server.url+'/#'+server.token);
    const status=await get('status');expect(status.version).toBe('1.11.0');
    report.version=status.version;report.stats=status.stats;
    const sample=await get('package?key='+encodeURIComponent(manual.sample('VAMPM_TEST_BUNDLE_KEY')));
    expect(sample.files).toHaveLength(1);
    expect(sample.files[0].rel).toBe(manual.sample('VAMPM_TEST_BUNDLE_PATH'));
    expect(sample.files[0].area).toBe('vault');
    expect(sample.resource_folders.some(g=>g.name===manual.sample('VAMPM_TEST_BUNDLE_NAME'))).toBe(true);
    report.resource_folder={name:sample.resource_folders[0].name,path:sample.files[0].rel,area:sample.files[0].area};
    const t=Date.now();await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    await expect(page.getByRole('heading',{name:'固定基础包',exact:true})).toBeVisible();
    report.settings_ms=Date.now()-t;
    await page.screenshot({path:'test-results/Windows-1.11-固定基础包设置.png'});
    await page.getByRole('button',{name:'管理固定基础包'}).click();
    await expect(page.getByLabel('筛选状态')).toHaveValue('pinned');
    await page.getByLabel('搜索资源').fill(manual.sample('VAMPM_TEST_PINNED_QUERY'));
    await expect(page.locator('.package-row')).toHaveCount(1);
    await expect(page.locator('.package-row .pin-mark')).toBeVisible();
    await page.locator('.package-row').click();
    await expect(page.locator('#drawer')).toContainText('固定基础包 · 常驻启用');
    await page.screenshot({path:'test-results/Windows-1.11-固定包详情.png'});
    await page.getByRole('button',{name:'关闭详情'}).click();
    await page.getByRole('button',{name:'▷ 启动方案',exact:true}).click();
    const card=page.locator('.profile').filter({hasText:'基础收藏'});
    await expect(card).toContainText('默认方案');await expect(card).toContainText('新增收藏自动启用');
    await card.getByRole('button',{name:'预览启用',exact:true}).click();
    const dialog=page.locator('#dialog');await expect(dialog).toBeVisible({timeout:90000});
    await expect(dialog).toContainText('固定基础包');
    const jobs=(await get('status')).jobs,job=jobs.find(j=>j.status==='done');
    const plan=(await get('job?id='+job.id)).result;
    report.preview={included:plan.included,files:plan.included_files,fixed:plan.fixed_packages,missing:plan.missing.length,
      moves:plan.count,unresolved:plan.unresolved_copies};
    await page.screenshot({path:'test-results/Windows-1.11-基础收藏预览.png'});
    await dialog.getByRole('button',{name:'取消',exact:true}).click();
    await page.setViewportSize({width:420,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect(report.errors).toEqual([]);
    await browser.close();browser=null;report.exit_ms=await exited(server);
    report.protected_unchanged=Object.fromEntries(protectedFiles.map(p=>[p,before[p]===digest(p)]));
    expect(Object.values(report.protected_unchanged).every(Boolean)).toBe(true);
    fs.writeFileSync('test-results/windows-layout.json',JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{if(browser)await browser.close();if(server)await exited(server);}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
