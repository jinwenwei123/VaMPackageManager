const manual=require('./manual_windows');
/* Native layout verification: no inference, package moves or saved label edits. */
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
  const prefs='data/library-preferences.json',original=fs.existsSync(prefs)?fs.readFileSync(prefs):null;
  const protectedFiles=['data/ai-settings.json','data/profiles.json','data/agent/conversation.json'];
  const before=Object.fromEntries(protectedFiles.map(file=>[file,digest(file)]));
  let browser,server;const report={errors:[]};
  try{
    execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
    for(let i=0;i<100&&!fs.existsSync('data/server.json');i++)await pause(100);
    server=JSON.parse(fs.readFileSync('data/server.json','utf8'));
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',e=>report.errors.push(e.message));
    await page.goto(server.url+'/#'+server.token);
    const status=await (await page.request.get(server.url+'/api/status',{headers:{'X-Session-Token':server.token}})).json();
    expect(status.version).toBe('1.8.0');report.version=status.version;report.packages=status.stats.packages;
    report.categories=status.stats.categories_all;expect(report.categories).not.toContain('依赖');
    await page.getByLabel('搜索资源').fill(manual.sample('VAMPM_TEST_COVER_QUERY'));
    await expect(page.locator('.package-row')).toHaveCount(1);
    await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
    const card=page.locator('.grid-view .package-row');await expect(card).toBeVisible();
    await expect(card.locator('.package-art img')).toBeVisible();
    report.grid=await card.evaluate(el=>({height:el.getBoundingClientRect().height,width:el.getBoundingClientRect().width,
      cover:el.querySelector('.package-art').getBoundingClientRect().height,clamp:getComputedStyle(el.querySelector('.package-summary')).webkitLineClamp}));
    expect(report.grid.height).toBeLessThan(335);expect(report.grid.clamp).toBe('2');
    const toggle=card.locator('[data-summary-toggle]');
    if(await toggle.isVisible()){
      await toggle.click();await expect(toggle).toHaveAttribute('aria-expanded','true');
      await expect(page.locator('#drawer-backdrop')).toBeHidden();
      await toggle.click();await expect(toggle).toHaveAttribute('aria-expanded','false');
      report.disclosure_checked=true;
    }
    await page.screenshot({path:'test-results/Windows-1.8-紧凑大图.png'});
    await card.locator('.package-summary').click();await expect(page.locator('#drawer-backdrop')).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'✧ 对话助手',exact:true}).click();
    await expect(page.getByRole('heading',{name:'对话助手',exact:true})).toBeVisible();
    report.chat=await page.locator('#chat-thread').evaluate(el=>({height:el.clientHeight,
      shell:document.querySelector('.chat-shell').clientHeight,bottom:innerHeight-document.querySelector('.chat-shell').getBoundingClientRect().bottom,
      outer_scroll:document.documentElement.scrollHeight>innerHeight+1}));
    expect(report.chat.height).toBeGreaterThan(690);expect(report.chat.outer_scroll).toBe(false);
    await page.setViewportSize({width:390,height:700});
    report.narrow=await page.evaluate(()=>({horizontal:document.documentElement.scrollWidth>innerWidth,vertical:document.documentElement.scrollHeight>innerHeight+1,thread:document.querySelector('#chat-thread').clientHeight}));
    expect(report.narrow.horizontal).toBe(false);expect(report.narrow.vertical).toBe(false);
    await expect(page.getByRole('button',{name:'发送 ↑'})).toBeInViewport();
    await page.locator('#navigation [data-page=library]').click();await expect(page.locator('footer')).toBeVisible();
    report.protected_files_unchanged=Object.fromEntries(protectedFiles.map(file=>[file,digest(file)===before[file]]));
    expect(Object.values(report.protected_files_unchanged).every(Boolean)).toBe(true);expect(report.errors).toEqual([]);
    await browser.close();browser=null;report.exit_ms=await exited(server);
    fs.writeFileSync('test-results/windows-compact.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  }finally{
    if(browser)await browser.close();if(server)await exited(server);
    if(original)fs.writeFileSync(prefs,original);else if(fs.existsSync(prefs))fs.unlinkSync(prefs);
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
