const manual=require('./manual_windows');
/* Native EXE, real library: inspect dialogs and initialization without AI calls. */
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
  throw Error('Server did not exit after closing the last page');
}
(async()=>{
  if(fs.existsSync('data/server.json'))throw Error('Close the manager first');
  const protectedFiles=['data/ai-settings.json','data/profiles.json','data/agent/conversation.json',
    'data/library-preferences.json','data/appearance.json','data/activation-choices.json',
    'data/base-packages.json','data/launch-settings.json','data/resource-folders.json'];
  const before=Object.fromEntries(protectedFiles.map(p=>[p,digest(p)]));
  const report={errors:[],generation_requests:[]};let browser,server;
  try{
    execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
    for(let i=0;i<100&&!fs.existsSync('data/server.json');i++)await pause(100);
    server=JSON.parse(fs.readFileSync('data/server.json','utf8'));
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',e=>report.errors.push(e.message));
    page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(ai|ai-queue\/(start|resume))$/.test(r.url()))report.generation_requests.push(r.url().split('/api/')[1]);});
    const headers={'X-Session-Token':server.token};
    const get=async route=>(await page.request.get(server.url+'/api/'+route,{headers})).json();
    await page.goto(server.url+'/#'+server.token);
    const status=await get('status');expect(status.version).toBe('1.12.0');
    report.version=status.version;report.stats=status.stats;
    const pending=await get('packages?state=unlabeled');
    report.unprocessed=pending.total;expect(pending.total).toBe(status.stats.packages);
    const queueBefore=status.summary_queue;
    const dialog=page.locator('#dialog');
    await page.locator('[data-action=ai-all]').click();
    await expect(dialog).toBeVisible();await expect(dialog).toContainText('全部资源');
    await expect(dialog.locator('[value=all]')).toBeChecked();
    await page.screenshot({path:'test-results/Windows-1.12-全部生成确认.png'});
    await dialog.locator('[value=missing]').check();
    await page.setViewportSize({width:420,height:844});
    await expect(dialog.locator('[value=missing]')).toBeChecked();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/Windows-1.12-补充生成窄屏.png'});
    await dialog.getByRole('button',{name:'取消',exact:true}).click();
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('[data-action=favorites]').click();
    await expect(page.getByRole('heading',{name:'收藏夹',exact:true})).toBeVisible();
    await page.locator('[data-action=ai-all]').click();
    await expect(dialog).toContainText('全部收藏');
    await expect(dialog.locator('[value=all]')).toBeChecked();
    await dialog.getByRole('button',{name:'取消',exact:true}).click();
    expect((await get('status')).summary_queue).toEqual(queueBefore);
    expect(report.generation_requests).toEqual([]);expect(report.errors).toEqual([]);
    await browser.close();browser=null;report.exit_ms=await exited(server);
    report.protected_unchanged=Object.fromEntries(protectedFiles.map(p=>[p,before[p]===digest(p)]));
    expect(Object.values(report.protected_unchanged).every(Boolean)).toBe(true);
    fs.writeFileSync('test-results/windows-summary-modes.json',JSON.stringify(report,null,2));
    console.log(JSON.stringify({version:report.version,unprocessed:report.unprocessed,exit_ms:report.exit_ms,ai_requests:report.generation_requests.length}));
  }finally{if(browser)await browser.close();if(server)await exited(server);}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
