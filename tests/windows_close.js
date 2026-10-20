const manual=require('./manual_windows');
/* Native Windows: real EXE + browser connections, read-only against the real library. */
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),{execFileSync}=require('child_process');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const exists=()=>fs.existsSync('data/server.json');
const alive=pid=>{try{process.kill(pid,0);return true;}catch{return false;}};
async function until(predicate,ms=15000){const deadline=Date.now()+ms;while(!predicate()){if(Date.now()>deadline)throw Error('Timed out waiting for process lifecycle');await delay(100);}}
async function launch(){
  await until(()=>!exists());
  execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
  await until(exists);return JSON.parse(fs.readFileSync('data/server.json','utf8'));
}
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const results=[];
  try{
    const s=await launch(),context=await browser.newContext();
    console.log('Native EXE started; testing two tabs and reload.');
    const first=await context.newPage(),second=await context.newPage();
    await first.goto(s.url+'/#'+s.token);await second.goto(s.url+'/#'+s.token);
    await first.getByRole('heading',{name:'资源库',exact:true}).waitFor();
    await second.getByRole('heading',{name:'资源库',exact:true}).waitFor();
    await first.reload();await first.getByRole('heading',{name:'资源库',exact:true}).waitFor();
    execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
    await second.close();await delay(6500);
    if(!alive(s.pid)||!exists()||JSON.parse(fs.readFileSync('data/server.json','utf8')).pid!==s.pid)throw Error('Refresh or another tab closed the service');
    const start=Date.now();await first.close();
    await until(()=>!alive(s.pid)&&!exists());
    results.push({case:'refresh, multiple tabs, final close',passed:true,exit_ms:Date.now()-start});
    console.log('Last-page close removed the native backend process.');
    await context.close();
    const other=await launch(),next=await browser.newContext(),page=await next.newPage();
    await page.goto(other.url+'/#'+other.token);await page.getByRole('heading',{name:'资源库',exact:true}).waitFor();
    const cdp=await next.newCDPSession(page),crashStart=Date.now();
    const crashed=cdp.send('Page.crash').catch(()=>{});
    await until(()=>!alive(other.pid)&&!exists());
    results.push({case:'crashed page without pagehide',passed:true,exit_ms:Date.now()-crashStart});
    await next.close();await crashed;
    fs.writeFileSync('test-results/windows-auto-exit.json',JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
