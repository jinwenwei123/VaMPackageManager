const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path');
const {spawn}=require('child_process');

async function fresh(browser,run){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-first-run-ui-'));
  const home=path.join(root,'manager'),game=path.join(root,'Chosen game');
  fs.mkdirSync(home);fs.mkdirSync(path.join(game,'AddonPackages'),{recursive:true});
  fs.writeFileSync(path.join(game,'AddonPackages','keep.txt'),'original');
  fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--no-browser'],{stdio:'ignore'});
  const context=await browser.newContext(),errors=[];
  let server;
  try{
    const file=path.join(home,'data/server.json');
    await expect.poll(()=>fs.existsSync(file),{timeout:10000}).toBe(true);
    server=JSON.parse(fs.readFileSync(file,'utf8'));
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/*models*',r=>r.fulfill({json:{models:[],cached:true}}));
    await page.goto(server.url+'/#'+server.token);
    await expect(page.locator('#onboarding')).toBeVisible();
    await run({page,root,home,game,context,server});
    expect(errors).toEqual([]);
    expect(fs.existsSync(path.join(root,'AddonPackages'))).toBe(false);
    expect(fs.existsSync(path.join(root,'.PackageManagerStorage'))).toBe(false);
  }finally{
    if(server)await context.request.post(server.url+'/api/shutdown',{headers:{'X-Session-Token':server.token},data:{}}).catch(()=>{});
    await context.close();
    if(proc.exitCode===null)await new Promise(resolve=>{const timer=setTimeout(()=>{proc.kill();resolve();},5000);proc.once('exit',()=>{clearTimeout(timer);resolve();});});
    fs.rmSync(root,{recursive:true,force:true});
  }
}

test('first launch can switch language, skip game, configure AI and stay unselected after reload',async({browser},testInfo)=>{
  await fresh(browser,async({page,home,game})=>{
    await expect(page.locator('#onboarding')).toContainText('第 1 步');
    await page.locator('#onboarding-language').selectOption('en');
    await expect(page.locator('#onboarding')).toContainText('Step 1');
    await page.screenshot({path:testInfo.outputPath('first-run-en.png')});
    await page.locator('[data-onboarding-action=skip_game]').click();
    await expect(page.locator('#onboarding')).toContainText('Step 2');
    await page.locator('[data-onboarding-action=configure_ai]').click();
    await expect(page.locator('#onboarding')).not.toBeVisible();
    await expect(page.locator('#settings-form')).toBeVisible();
    await page.locator('#ai-provider').selectOption('compatible');
    const form=page.locator('#settings-form');
    await form.locator('[name=base_url]').fill('https://example.invalid/v1');
    await form.locator('[name=api_model]').fill('demo-model');
    await form.locator('[name=api_summary_model]').fill('demo-model');
    await form.locator('button[type=submit]').click();
    await expect(page.locator('#toast')).toContainText('saved');
    await page.reload();
    await expect(page.locator('#workspace-offline')).toBeVisible();
    await expect(page.locator('#onboarding')).not.toBeVisible();
    expect(JSON.parse(fs.readFileSync(path.join(home,'data/game-directories.json'))).items).toEqual([]);
    expect(fs.existsSync(path.join(game,'.PackageManagerStorage'))).toBe(false);
    // A later settings selection inherits the AI connection configured without a game.
    await page.locator('.nav-item[data-page=settings]').click();
    const directory=page.locator('#directory-add-form');
    await directory.locator('[name=path]').fill(game);
    await directory.locator('button[type=submit]').click();
    await page.locator('[data-workspace-switch=original]').click();
    await expect(page.locator('#workspace-name')).toHaveText('Chosen game');
    await page.locator('.nav-item[data-page=settings]').click();
    await expect(page.locator('#settings-form [name=api_model]')).toHaveValue('demo-model');
    await expect(page.locator('#onboarding')).not.toBeVisible();
  });
});

test('native folder selection can cancel; invalid roots create nothing; confirmation enables only chosen directory',async({browser},testInfo)=>{
  await fresh(browser,async({page,root,game,home})=>{
    let selected=null;
    await page.route('**/api/game-directories/browse',r=>r.fulfill({json:selected?{path:selected,name:path.basename(selected),cancelled:false}:{cancelled:true}}));
    const form=page.locator('#onboarding-directory-form');
    await form.locator('[data-directory-browse]').click();
    await expect(form.locator('[name=path]')).toHaveValue('');
    const invalid=path.join(root,'Invalid');fs.mkdirSync(invalid);
    await form.locator('[name=path]').fill(invalid);
    await form.locator('button[type=submit]').click();
    await expect(page.locator('#onboarding-error')).toBeVisible();
    expect(fs.readdirSync(invalid)).toEqual([]);
    selected=game;
    await form.locator('[data-directory-browse]').click();
    await expect(form.locator('[name=name]')).toHaveValue('Chosen game');
    expect(fs.existsSync(path.join(game,'.PackageManagerStorage'))).toBe(false);
    await page.screenshot({path:testInfo.outputPath('first-run-zh.png')});
    await form.locator('button[type=submit]').click();
    await expect(page.locator('#onboarding')).toContainText('第 2 步');
    await expect(page.locator('#workspace-name')).toHaveText('Chosen game');
    expect(fs.existsSync(path.join(game,'.PackageManagerStorage'))).toBe(true);
    expect(fs.readFileSync(path.join(game,'AddonPackages','keep.txt'),'utf8')).toBe('original');
    await page.locator('[data-onboarding-action=finish]').click();
    await page.reload();await expect(page.locator('#onboarding')).not.toBeVisible();
    expect(JSON.parse(fs.readFileSync(path.join(home,'data/onboarding.json'))).stage).toBe('done');
  });
});

test('Escape skips setup durably without selecting any directory',async({browser})=>{
  await fresh(browser,async({page,home})=>{
    await page.keyboard.press('Escape');
    await expect(page.locator('#dialog')).not.toBeVisible();
    await page.reload();await expect(page.locator('#workspace-offline')).toBeVisible();
    await expect(page.locator('#onboarding')).not.toBeVisible();
    expect(JSON.parse(fs.readFileSync(path.join(home,'data/game-directories.json'))).current).toBe(null);
  });
});
