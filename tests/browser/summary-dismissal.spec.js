const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),net=require('net'),{spawn}=require('child_process');

test('completion dismissal survives port changes while unfinished progress stays visible after restart',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-summary-dismiss-'));
  const home=path.join(root,'tool'),game=path.join(root,'game'),data=path.join(home,'data');
  fs.mkdirSync(data,{recursive:true});fs.mkdirSync(path.join(game,'AddonPackages'),{recursive:true});
  fs.cpSync('web',path.join(home,'web'),{recursive:true});
  fs.writeFileSync(path.join(data,'summary-queue.json'),JSON.stringify({scope:'selected',mode:'all',target:'both',
    run_id:'completed-fixture',status:'done',keys:[],current:1,total:1,updated:1,skipped:0,message:'本轮已完成'}));
  let proc,server;
  const context=await browser.newContext(),page=await context.newPage();
  const start=async()=>{
    proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
    const file=path.join(data,'server.json');
    await expect.poll(()=>fs.existsSync(file),{timeout:10000}).toBe(true);
    server=JSON.parse(fs.readFileSync(file,'utf8'));
    await page.goto(server.url+'/#'+server.token);
    await page.locator('#navigation [data-page=library]').click();
    await expect(page.getByRole('heading',{name:'资源库',exact:true})).toBeVisible();
  };
  const stop=async()=>{
    await page.goto('about:blank');
    try{
      const response=await context.request.post(server.url+'/api/shutdown',{headers:{'X-Session-Token':server.token},data:{}});
      expect(response.ok()).toBe(true);
    }catch(error){
      // A normal server exit can close the response before its body is read.
      if(!/aborted|ECONNRESET|socket hang up/i.test(error.message))throw error;
    }
    await expect.poll(()=>proc.exitCode!==null,{timeout:10000}).toBe(true);
    expect(proc.exitCode).toBe(0);
  };
  let reserved;
  try{
    await start();
    await expect(page.locator('.summary-queue-card')).toContainText('所选资源 · 简介＋标签 · 全部生成 / 重新生成');
    await page.getByRole('button',{name:'关闭生成完成提示',exact:true}).click();
    await expect(page.locator('.summary-queue-card')).toHaveCount(0);
    const previous=server.url;
    await stop();
    // Keep the old port occupied so the launcher's next port must be different.
    reserved=net.createServer();
    await new Promise((resolve,reject)=>reserved.once('error',reject).listen(Number(new URL(previous).port),'127.0.0.1',resolve));
    await start();expect(server.url).not.toBe(previous);
    await expect(page.locator('.summary-queue-card')).toHaveCount(0);
    await stop();
    // An unfinished run keeps its progress when the next app instance opens.
    fs.writeFileSync(path.join(data,'summary-queue.json'),JSON.stringify({scope:'selected',mode:'all',target:'both',
      run_id:'unfinished-fixture',status:'paused',keys:['first','second','third'],current:1,total:3,updated:1,skipped:0,message:'已暂停'}));
    await start();
    await expect(page.locator('.summary-queue-card')).toContainText('已暂停');
    await expect(page.locator('.summary-queue-counts')).toContainText('已处理 1 / 3');
    await expect(page.getByRole('button',{name:'关闭生成完成提示',exact:true})).toHaveCount(0);
    await expect(page.getByRole('button',{name:'继续生成',exact:true})).toBeVisible();
    await stop();
    await start();
    await expect(page.locator('.summary-queue-card')).toContainText('已暂停');
    await expect(page.locator('.summary-queue-counts')).toContainText('已处理 1 / 3');
    await stop();
  }finally{
    reserved?.close();
    await context.close().catch(()=>{});
    if(proc&&proc.exitCode===null)proc.kill();
    if(proc)await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
