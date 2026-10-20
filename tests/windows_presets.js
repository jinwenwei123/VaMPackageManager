const manual=require('./manual_windows');
/* Actual Windows launcher and library. Read UI only; never save settings or call AI. */
const {chromium,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),crypto=require('crypto'),{execFileSync}=require('child_process');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const digest=p=>fs.existsSync(p)?crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex'):null;
async function exited(server){
  const start=Date.now();
  for(let n=0;n<300;n++){
    let alive=true;try{process.kill(server.pid,0);}catch{alive=false;}
    if(!alive&&!fs.existsSync('data/server.json'))return Date.now()-start;
    await pause(100);
  }
  throw Error('Manager did not exit after the last page closed');
}
(async()=>{
  if(fs.existsSync('data/server.json'))throw Error('Close the manager first');
  const files=['ai-settings.json','agent/conversation.json','profiles.json','activation-choices.json',
    'base-packages.json','launch-settings.json','resource-folders.json','appearance.json',
    'library-preferences.json','summary-queue.json','chat-presets.json'];
  const before=Object.fromEntries(files.map(p=>[p,digest('data/'+p)]));
  const report={errors:[],mutation_requests:[]};let browser,server;
  try{
    execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
    for(let i=0;i<100&&!fs.existsSync('data/server.json');i++)await pause(100);
    server=JSON.parse(fs.readFileSync('data/server.json','utf8'));
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',e=>report.errors.push(e.message));
    page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(agent\/send|chat-presets(?:\/draft)?|settings)$/.test(r.url()))report.mutation_requests.push(r.url().split('/api/')[1]);});
    const headers={'X-Session-Token':server.token};
    const get=async route=>(await page.request.get(server.url+'/api/'+route,{headers})).json();
    await page.goto(server.url+'/#'+server.token);
    const status=await get('status');expect(status.version).toBe('1.19.0');report.version=status.version;
    await expect(page.locator('.package-row')).toHaveCount(42);
    const presets=await get('chat-presets'),settings=await get('settings');
    report.presets=presets.items.length;report.featured=presets.items.filter(p=>p.featured).length;
    expect(report.presets).toBe(10);expect(report.featured).toBe(5);
    const stored=JSON.parse(fs.readFileSync('data/ai-settings.json','utf8'));
    expect(settings.api_summary_model).toBe(stored.api_summary_model||stored.api_model);
    report.api_summary_inherits_legacy=true;
    await page.getByRole('button',{name:'✧ 对话助手',exact:true}).click();
    await expect(page.locator('.chat-preset-shortcuts button')).toHaveCount(5);
    await page.getByRole('button',{name:'查看全部对话预设'}).click();
    const panel=page.locator('#preset-dialog');await expect(panel.locator('.preset-row')).toHaveCount(10);
    await page.screenshot({path:'test-results/Windows-1.13-对话预设面板.png'});
    const person=presets.items.find(p=>p.id==='people');
    await panel.locator('[data-preset-use=people]').click();
    await expect(page.getByLabel('发送给助手的消息')).toHaveValue(person.prompt);
    await page.getByRole('button',{name:'查看全部对话预设'}).click();
    await panel.getByRole('button',{name:'＋ 新增预设',exact:true}).click();
    await expect(panel.getByLabel('标题',{exact:true})).toBeVisible();
    await expect(panel.getByLabel('提示词',{exact:true})).toBeVisible();
    await expect(panel.getByRole('button',{name:'✧ AI 生成提示词',exact:true})).toBeVisible();
    await page.setViewportSize({width:390,height:700});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/Windows-1.13-预设编辑窄屏.png'});
    await page.keyboard.press('Escape');await page.setViewportSize({width:1440,height:1000});
    await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    for(const provider of ['deepseek','compatible']){
      await page.locator('#ai-provider').selectOption(provider);
      await expect(page.locator('[name=api_model]')).toBeVisible();
      await expect(page.locator('[name=api_summary_model]')).toBeVisible();
      await expect(page.locator('[name=api_model]')).toHaveValue(settings.api_model);
      await expect(page.locator('[name=api_summary_model]')).toHaveValue(settings.api_summary_model);
    }
    await page.screenshot({path:'test-results/Windows-1.13-API双模型.png',fullPage:true});
    expect(report.errors).toEqual([]);expect(report.mutation_requests).toEqual([]);
    await browser.close();browser=null;report.exit_ms=await exited(server);
    report.protected_unchanged=Object.fromEntries(files.map(p=>[p,before[p]===digest('data/'+p)]));
    expect(Object.values(report.protected_unchanged).every(Boolean)).toBe(true);
    fs.writeFileSync('test-results/windows-presets.json',JSON.stringify(report,null,2));
    console.log(JSON.stringify({version:report.version,presets:report.presets,featured:report.featured,exit_ms:report.exit_ms,mutations:report.mutation_requests.length}));
  }finally{if(browser)await browser.close();if(server)await exited(server);}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
