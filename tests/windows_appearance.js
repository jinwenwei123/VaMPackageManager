const manual=require('./manual_windows');
/* Native EXE smoke test. Changes appearance only, then restores the original choice. */
const {chromium,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),{execFileSync}=require('child_process');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function boot(){
  execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
  for(let i=0;i<80;i++){
    if(fs.existsSync('data/server.json'))return JSON.parse(fs.readFileSync('data/server.json','utf8'));
    await pause(100);
  }
  throw Error('EXE did not start');
}
async function stopped(pid){
  const start=Date.now();
  for(let i=0;i<150;i++){
    let alive=true;try{process.kill(pid,0);}catch{alive=false;}
    if(!alive&&!fs.existsSync('data/server.json'))return Date.now()-start;
    await pause(100);
  }
  throw Error('Background did not exit');
}
(async()=>{
  let browser,original,server;
  const errors=[],report={};
  try{
    server=await boot();
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    let page=await browser.newPage({viewport:{width:1480,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(server.url+'/#'+server.token);
    await page.getByRole('heading',{name:'资源库',exact:true}).waitFor();
    const headers={'X-Session-Token':server.token};
    original=await (await page.request.get(server.url+'/api/appearance',{headers})).json();
    await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    await page.getByRole('button',{name:'海洋蓝',exact:true}).click();
    await page.locator('[data-mode-choice=dark]').click();
    await expect(page.locator('#appearance-status')).toHaveText('已自动保存');
    await expect.poll(async()=> (await (await page.request.get(server.url+'/api/appearance',{headers})).json())).toEqual({palette:'ocean',mode:'dark'});
    await page.screenshot({path:'test-results/Windows主题设置-深色蓝.png',fullPage:true});
    await page.locator('#navigation [data-page=library]').click();
    await page.getByLabel('搜索资源').fill(manual.sample('VAMPM_TEST_PACKAGE_QUERY'));
    const downloaded=page.locator('.package-row[data-key="14mhz.plugin-view2atom.6"]');
    await expect(downloaded).toHaveCount(1);
    await downloaded.locator('.warning-mark').click();
    await expect(page.locator('#drawer')).toContainText('包内名称');
    await expect(page.locator('#drawer .notice.warning')).toContainText('文件已保留');
    await page.screenshot({path:'test-results/Windows包内名称差异提示.png',fullPage:false});
    report.appearance={palette:'ocean',mode:'dark'};
    report.first_pid=server.pid;report.first_port=new URL(server.url).port;
    await browser.close();browser=null;
    report.first_exit_ms=await stopped(server.pid);
    server=await boot();
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    page=await browser.newPage({viewport:{width:1480,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(server.url+'/#'+server.token,{waitUntil:'domcontentloaded'});
    await expect(page.locator('html')).toHaveAttribute('data-palette','ocean');
    await expect(page.locator('html')).toHaveAttribute('data-mode','dark');
    await page.getByRole('heading',{name:'资源库',exact:true}).waitFor();
    report.second_pid=server.pid;report.second_port=new URL(server.url).port;
    report.persisted_after_restart=true;
    await page.request.post(server.url+'/api/appearance',{headers:{'X-Session-Token':server.token},data:original});
    await expect(page.locator('html')).toHaveAttribute('data-palette',original.palette);
    const status=await (await page.request.get(server.url+'/api/status',{headers:{'X-Session-Token':server.token}})).json();
    report.stats={packages:status.stats.packages,favorites:status.stats.favorites,warnings:status.stats.warnings};
    await browser.close();browser=null;
    report.second_exit_ms=await stopped(server.pid);
    report.errors=errors;expect(errors).toEqual([]);
    fs.writeFileSync('test-results/windows-appearance.json',JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{
    if(browser){
      if(original&&server){
        try{await browser.contexts()[0].request.post(server.url+'/api/appearance',{headers:{'X-Session-Token':server.token},data:original});}catch{}
      }
      await browser.close();
    }
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
