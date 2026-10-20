const manual=require('./manual_windows');
/* Native EXE verification; changes only cosmetic settings and restores their exact files. */
const {chromium,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),{execFileSync}=require('child_process');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function boot(){
  execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
  for(let i=0;i<100;i++){
    if(fs.existsSync('data/server.json'))return JSON.parse(fs.readFileSync('data/server.json','utf8'));
    await pause(100);
  }
  throw Error('EXE did not start');
}
async function stopped(pid){
  const start=Date.now();
  for(let i=0;i<180;i++){
    let alive=true;try{process.kill(pid,0);}catch{alive=false;}
    if(!alive&&!fs.existsSync('data/server.json'))return Date.now()-start;
    await pause(100);
  }
  throw Error('Background did not exit');
}
(async()=>{
  if(fs.existsSync('data/server.json'))throw Error('Close existing package manager pages before this test');
  const originals=new Map(['data/appearance.json','data/library-preferences.json'].map(file=>[file,fs.existsSync(file)?fs.readFileSync(file):null]));
  const errors=[],report={};let browser,server;
  const launchBrowser=()=>chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try{
    server=await boot();browser=await launchBrowser();
    let page=await browser.newPage({viewport:{width:1480,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(server.url+'/#'+server.token);
    await page.getByRole('heading',{name:'资源库',exact:true}).waitFor();
    const originalStatus=await (await page.request.get(server.url+'/api/status',{headers:{'X-Session-Token':server.token}})).json();
    report.stats={packages:originalStatus.stats.packages,favorites:originalStatus.stats.favorites,warnings:originalStatus.stats.warnings};
    await page.getByRole('button',{name:'☷ 列表',exact:true}).click();
    await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    await page.getByRole('button',{name:'海洋蓝',exact:true}).click();
    await page.locator('[data-mode-choice=dark]').click();
    await expect(page.locator('#appearance-status')).toHaveText('已自动保存');
    await page.locator('#navigation [data-page=library]').click();
    const colors=await page.evaluate(()=>({logo:getComputedStyle(document.querySelector('.brand-icon rect')).fill,button:getComputedStyle(document.querySelector('.button.primary')).backgroundColor}));
    expect(colors.logo).toBe(colors.button);report.logo=colors.logo;
    const bar=page.locator('[data-floating-pager] .pagination'),slot=page.locator('[data-floating-pager]');
    await expect(bar).toHaveClass(/is-floating/);
    report.floating=await bar.evaluate(el=>({background:getComputedStyle(el).backgroundColor,blur:getComputedStyle(el).backdropFilter,bottom:innerHeight-el.getBoundingClientRect().bottom}));
    expect(report.floating.background).toContain('0.76');
    await page.screenshot({path:'test-results/Windows悬浮分页-深色蓝.png'});
    const pages=Number(await slot.getAttribute('data-count')),start=Date.now();
    await bar.getByRole('button',{name:'最后一页',exact:true}).click();
    await expect(bar.getByLabel('跳转页码')).toHaveValue(String(pages));
    report.last_page_ms=Date.now()-start;report.pages=pages;
    await bar.getByRole('button',{name:'第一页',exact:true}).click();
    await expect(bar.getByLabel('跳转页码')).toHaveValue('1');
    await bar.getByLabel('跳转页码').fill('12');await bar.getByLabel('跳转页码').press('Enter');
    await expect(slot).toHaveAttribute('data-current','12');
    await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
    await expect(bar).not.toHaveClass(/is-floating/);
    await page.screenshot({path:'test-results/Windows分页归位-深色蓝.png'});
    await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
    await page.getByLabel('排序',{exact:true}).selectOption('size');
    await page.getByLabel('筛选状态').selectOption('active');
    await expect.poll(async()=> (await (await page.request.get(server.url+'/api/library-preferences',{headers:{'X-Session-Token':server.token}})).json())).toEqual({view:'grid',sort:'size'});
    await page.screenshot({path:'test-results/Windows悬浮分页-大图.png'});
    report.first_pid=server.pid;report.first_port=new URL(server.url).port;
    await browser.close();browser=null;report.first_exit_ms=await stopped(server.pid);

    server=await boot();browser=await launchBrowser();
    page=await browser.newPage({viewport:{width:1480,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(server.url+'/#'+server.token);
    await expect(page.getByRole('button',{name:'⊞ 大图',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(page.getByLabel('排序',{exact:true})).toHaveValue('size');
    await expect(page.getByLabel('筛选状态')).toHaveValue('');
    await expect(page.locator('html')).toHaveAttribute('data-palette','ocean');
    await expect(page.locator('html')).toHaveAttribute('data-mode','dark');
    report.second_pid=server.pid;report.second_port=new URL(server.url).port;report.persisted_after_restart=true;
    await page.locator('#navigation [data-action=favorites]').click();
    await expect(page.getByLabel('排序',{exact:true})).toHaveValue('size');
    await expect(page.getByRole('button',{name:'⊞ 大图',exact:true})).toHaveAttribute('aria-pressed','true');
    await page.setViewportSize({width:760,height:900});
    await expect.poll(async()=>{const b=await page.locator('[data-floating-pager] .pagination').boundingBox();return b.x+b.width;}).toBeLessThanOrEqual(760);
    const bounds=await page.locator('[data-floating-pager] .pagination').boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.x+bounds.width).toBeLessThanOrEqual(760);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:'test-results/Windows悬浮分页-收藏窄窗口.png'});
    await browser.close();browser=null;report.second_exit_ms=await stopped(server.pid);
    report.errors=errors;expect(errors).toEqual([]);
    fs.writeFileSync('test-results/windows-browsing.json',JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{
    if(browser)await browser.close();
    if(server)await stopped(server.pid);
    for(const [file,bytes] of originals){if(bytes)fs.writeFileSync(file,bytes);else if(fs.existsSync(file))fs.unlinkSync(file);}
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
