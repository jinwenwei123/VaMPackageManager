const manual=require('./manual_windows');
/* Real Windows UI: read package content and model catalogs; never generate labels. */
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
  if(fs.existsSync('data/server.json'))throw Error('Close existing package manager pages before this test');
  const prefs='data/library-preferences.json',original=fs.existsSync(prefs)?fs.readFileSync(prefs):null;
  const settingsBefore=digest('data/ai-settings.json');let browser,server;
  const report={errors:[]};
  try{
    execFileSync(path.resolve('VaMPackageManager.exe'),['--no-browser'],{stdio:'ignore',timeout:10000});
    for(let i=0;i<100&&!fs.existsSync('data/server.json');i++)await pause(100);
    server=JSON.parse(fs.readFileSync('data/server.json','utf8'));
    browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    const page=await browser.newPage({viewport:{width:1480,height:1000}});
    page.on('pageerror',e=>report.errors.push(e.message));
    await page.goto(server.url+'/#'+server.token);
    const status=await (await page.request.get(server.url+'/api/status',{headers:{'X-Session-Token':server.token}})).json();
    expect(status.version).toBe('1.7.0');report.version=status.version;report.packages=status.stats.packages;
    await page.getByLabel('搜索资源').fill(manual.sample('VAMPM_TEST_COVER_QUERY'));
    await expect(page.locator('.package-row')).toHaveCount(1);
    await page.getByRole('button',{name:'☷ 列表',exact:true}).click();
    await page.locator('.package-summary').click();
    const img=page.locator('#drawer .detail-cover img');await expect(img).toBeVisible();
    report.preview=await img.evaluate(el=>({fit:getComputedStyle(el).objectFit,width:el.naturalWidth,height:el.naturalHeight,
      contained:el.getBoundingClientRect().bottom<=el.parentElement.getBoundingClientRect().bottom}));
    expect(report.preview.fit).toBe('contain');expect(report.preview.contained).toBe(true);
    await page.screenshot({path:'test-results/Windows-1.7-完整详情.png'});
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
    await page.locator('.package-art').click();await expect(page.locator('#drawer-backdrop')).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    await expect(page.locator('[data-model-status=codex]')).toContainText('已读取',{timeout:30000});
    await expect(page.locator('[data-model-status=api]')).toContainText('已读取',{timeout:30000});
    report.codex=await page.locator('[name=codex_summary_model] option').evaluateAll(opts=>opts.map(o=>({id:o.value,label:o.textContent})));
    report.api=await page.locator('[name=api_model] option').evaluateAll(opts=>opts.map(o=>({id:o.value,label:o.textContent})));
    expect(report.codex.length).toBeGreaterThan(2);expect(report.codex.some(m=>m.label.includes('CLI 隐藏'))).toBe(true);
    expect(report.api.find(m=>m.id==='deepseek-flash').label).toContain('识图');
    await expect(page.locator('[name=api_key]')).toHaveValue('');
    const provider=await page.locator('#ai-provider').inputValue();
    await page.locator('#ai-provider').selectOption('deepseek');
    const model=page.locator('[name=api_model]'),selected=await model.inputValue();
    await model.selectOption('deepseek-v4-pro');
    await expect(page.locator('[data-model-tags=api_model]')).toContainText('仅文字');
    await model.selectOption(selected);
    await page.getByRole('button',{name:'刷新 API 模型'}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('已读取');
    await expect(model).toHaveValue(selected);
    await page.locator('#api-settings').scrollIntoViewIfNeeded();
    await page.screenshot({path:'test-results/Windows-1.7-模型能力.png'});
    await page.locator('#ai-provider').selectOption(provider);
    report.ai_settings_unchanged=settingsBefore===digest('data/ai-settings.json');
    expect(report.ai_settings_unchanged).toBe(true);expect(report.errors).toEqual([]);
    await browser.close();browser=null;report.exit_ms=await exited(server);
    fs.writeFileSync('test-results/windows-details.json',JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{
    if(browser)await browser.close();
    if(server)await exited(server);
    if(original)fs.writeFileSync(prefs,original);else if(fs.existsSync(prefs))fs.unlinkSync(prefs);
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
