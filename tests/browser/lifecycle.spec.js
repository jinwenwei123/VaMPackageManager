const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn}=require('child_process');

async function launch(){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-lifecycle-'));
  const home=path.join(root,'tool'),game=path.join(root,'game');
  fs.mkdirSync(home);fs.mkdirSync(game);fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
  const file=path.join(home,'data/server.json');
  await expect.poll(()=>fs.existsSync(file),{timeout:10000}).toBe(true);
  const state=JSON.parse(fs.readFileSync(file,'utf8'));
  return {proc,file,state,async close(){
    if(proc.exitCode===null)proc.kill();
    await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }};
}

test('refresh and remaining tabs keep the service alive; closing the last page frees its process',async({browser})=>{
  const server=await launch(),context=await browser.newContext();
  const {url,token,pid}=server.state;
  try{
    const first=await context.newPage(),second=await context.newPage();
    await first.goto(url+'/#'+token);await second.goto(url+'/#'+token);
    await expect(first.getByRole('heading',{name:'主页',exact:true})).toBeVisible();
    await expect(second.getByRole('heading',{name:'主页',exact:true})).toBeVisible();
    await first.reload();
    await expect(first.getByRole('heading',{name:'主页',exact:true})).toBeVisible();
    await second.close();
    await first.waitForTimeout(6500);
    expect(server.proc.exitCode).toBe(null);
    expect(JSON.parse(fs.readFileSync(server.file,'utf8')).pid).toBe(pid);
    await first.close();
    await expect.poll(()=>server.proc.exitCode,{timeout:12000}).toBe(0);
    expect(fs.existsSync(server.file)).toBe(false);
  }finally{await context.close();await server.close();}
});

test('a crashed renderer also releases the service without a close notification',async({browser})=>{
  const server=await launch(),context=await browser.newContext();
  try{
    const page=await context.newPage();await page.goto(server.state.url+'/#'+server.state.token);
    await expect(page.getByRole('heading',{name:'主页',exact:true})).toBeVisible();
    const cdp=await context.newCDPSession(page);
    const crashed=cdp.send('Page.crash').catch(()=>{});
    await expect.poll(()=>server.proc.exitCode,{timeout:15000}).toBe(0);
    expect(fs.existsSync(server.file)).toBe(false);
    await context.close();await crashed;
  }finally{await context.close();await server.close();}
});
