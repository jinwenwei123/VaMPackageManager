const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn,spawnSync}=require('child_process');

test('English UI preserves Chinese data and filenames, switches back, and persists across launches',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-language-'));
  const home=path.join(root,'tool'),game=path.join(root,'中文游戏目录'),data=path.join(home,'data');
  const seed=spawnSync('python3',['-c',`
import sys,shutil,json
from pathlib import Path
from test_manager import var_bytes
from vampm.server import Application
from vampm.util import Job,atomic_json
home,game=map(Path,sys.argv[1:])
(game/'AddonPackages/中文文件夹').mkdir(parents=True)
(game/'AddonPackages/中文文件夹/作者.人物.1.var').write_bytes(var_bytes('作者.人物.1'))
home.mkdir();shutil.copytree('web',home/'web')
app=Application(game,home);app.c.scan(Job('fixture'))
app.c.label('作者.人物.1','人物','已有中文简介保持原样',tags=['可爱风','长发'])
app.agent._entry('user','原来的中文对话')
app.agent._entry('assistant','原来的中文回复')
app.agent._save()
presets=app.chat_presets
presets.save(dict(action='save',revision=presets.get()['revision'],title='推荐人物卡',prompt='我的自定义中文提示'))
presets.save(dict(action='save',revision=presets.get()['revision'],id='hair',title='我改过的发型预设',prompt='保留我的原文'))
app.ops.save_profile('基础收藏',[],favorites=True)
atomic_json(app.c.data/'summary-queue.json',dict(scope='all',mode='all',target='both',run_id='fixture',status='paused',current=1,total=3,updated=1,skipped=0,message='已暂停，当前批次结果已保存。'))
`,home,game],{env:{...process.env,PYTHONPATH:'src:tests'},encoding:'utf8'});
  expect(seed.status,seed.stderr).toBe(0);
  const original=fs.readFileSync(path.join(game,'AddonPackages/中文文件夹/作者.人物.1.var'));
  const profilesBefore=fs.readFileSync(path.join(data,'profiles.json'));
  const context=await browser.newContext(),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/ai-models?**',route=>route.fulfill({json:{models:[{id:'fixture',name:'fixture',tags:['识图','推理','64K 上下文']}],provider:'codex'}}));
  let proc,server;
  const start=async()=>{
    proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
    await expect.poll(()=>fs.existsSync(path.join(data,'server.json'))).toBe(true);
    server=JSON.parse(fs.readFileSync(path.join(data,'server.json'),'utf8'));
    await page.goto(server.url+'/#'+server.token);
    await expect(page.locator('.home-grid')).toBeVisible();
  };
  const stop=async()=>{
    await page.goto('about:blank');
    try{
      await context.request.post(server.url+'/api/shutdown',{headers:{'X-Session-Token':server.token},data:{}});
    }catch(error){
      // The isolated server can exit after sending headers but before the
      // client finishes reading. Still require a successful process exit below.
      if(!/aborted|ECONNRESET|socket hang up/i.test(error.message))throw error;
    }
    await expect.poll(()=>proc.exitCode!==null,{timeout:10000}).toBe(true);
    expect(proc.exitCode).toBe(0);
  };
  const go=async(id,title)=>{
    await page.locator(`[data-page="${id}"]`).first().click();
    await expect(page.getByRole('heading',{name:title,exact:true}).first()).toBeVisible();
  };
  const missing=new Set();
  const record=async()=>{for(const key of await page.evaluate(()=>[...VaMPMI18n.missing]))missing.add(key);};
  try{
    await start();
    await go('settings','AI 与设置');
    await page.locator('#interface-language').selectOption('en');
    await expect(page.locator('html')).toHaveAttribute('lang','en');
    await expect(page.getByRole('heading',{name:'AI & Settings',exact:true})).toBeVisible();
    await expect(page.locator('#interface-language')).toHaveValue('en');
    await expect(page.locator('[data-model-tags=codex_model]')).toContainText('Vision');
    await record();
    await go('home','Home');
    // Render the active-profile card with the real profile identity, without
    // activating a profile or moving fixture files just to test its display.
    expect(await page.evaluate(()=>{
      const profiles=homeData.profiles.value;
      profiles[0].is_active=true;
      try{return homeCards().profiles;}finally{profiles[0].is_active=false;}
    })).toContain('Base favorites');
    expect(await page.evaluate(()=>profileDisplayName({is_default:true,name:'我改名的方案'}))).toBe('我改名的方案');
    expect(await page.locator('.brand').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    await page.screenshot({path:'test-results/language-english-home.png',fullPage:true});
    await page.locator('[data-home-customize]').click();
    await expect(page.getByRole('heading',{name:'Customize home',exact:true})).toBeVisible();
    await record();await page.locator('#dialog [data-action=close-modal]').click();
    await go('library','Library');
    await expect(page.locator('.summary-queue-card')).toContainText('Paused. Current batch results saved.');
    await page.locator('[data-category="人物"]').click();
    await expect(page.locator('.package-row')).toHaveCount(1);
    await expect(page.locator('.package-row')).toContainText('Characters');
    await expect(page.locator('.package-row')).toContainText('已有中文简介保持原样');
    await expect(page.locator('.package-row')).toContainText('可爱风');
    await page.locator('.package-name button').click();
    await expect(page.locator('#drawer')).toContainText('中文文件夹');
    await expect(page.locator('#drawer')).toContainText('作者.人物.1.var');
    await page.locator('#drawer details').first().locator('summary').click();
    // English display labels must never become request values or directory names.
    await expect(page.locator('#label-form select')).toHaveValue('人物');
    const save=page.waitForResponse(r=>r.url().endsWith('/api/label')&&r.request().method()==='POST');
    await page.locator('#label-form button[type=submit]').click();
    expect((await save).ok()).toBe(true);
    await expect(page.locator('#label-form select')).toHaveValue('人物');
    await record();await page.locator('[data-action=close-drawer]').click();
    await go('assistant','Assistant');
    await expect(page.locator('#chat-thread')).toContainText('原来的中文对话');
    await expect(page.locator('#chat-thread')).toContainText('原来的中文回复');
    const shortcuts=page.locator('.chat-preset-shortcuts');
    await expect(shortcuts.getByRole('button',{name:'Recommend characters',exact:true})).toBeVisible();
    await expect(shortcuts.getByRole('button',{name:'我改过的发型预设',exact:true})).toBeVisible();
    await shortcuts.getByRole('button',{name:'Recommend characters',exact:true}).click();
    await expect(page.locator('#chat-input')).toHaveValue(/^Recommend a few character appearance packages/);
    await page.locator('[data-preset-action=open]').click();
    const presets=page.locator('#preset-dialog');
    await expect(presets.locator('.preset-row')).toHaveCount(11);
    await expect(presets.locator('[data-preset-id=people]')).toContainText('Recommend characters');
    await expect(presets.locator('.preset-row').last()).toContainText('我的自定义中文提示');
    await presets.locator('[data-preset-id=people] [data-preset-action=edit]').click();
    await expect(presets.locator('[name=title]')).toHaveValue('Recommend characters');
    await expect(presets.locator('[name=prompt]')).toHaveValue(/^Recommend a few character appearance packages/);
    await presets.locator('button[type=submit]').click();
    await expect(presets.locator('[data-preset-id=people]')).toContainText('Recommend characters');
    await presets.locator('[data-preset-action=close]').click();
    await record();
    for(const [id,title] of [['dependencies','Dependencies'],['duplicates','Duplicates'],['profiles','Launch profiles'],['history','History']]){
      await go(id,title);await record();
      if(id==='profiles'){
        await expect(page.locator('.profile.is-default h3')).toContainText('Base favorites');
        await expect(page.locator('.profile:not(.is-default) h3')).toContainText('基础收藏');
      }
    }
    expect([...missing]).toEqual([]);
    expect(errors).toEqual([]);
    await page.setViewportSize({width:420,height:900});
    await go('settings','AI & Settings');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/language-english-mobile.png',fullPage:true});
    await stop();await start();
    await expect(page.locator('html')).toHaveAttribute('lang','en');
    await go('settings','AI & Settings');
    await page.locator('#interface-language').selectOption('zh-CN');
    await expect(page.getByRole('heading',{name:'AI 与设置',exact:true})).toBeVisible();
    await go('assistant','对话助手');
    await expect(shortcuts.getByRole('button',{name:'推荐人物卡',exact:true})).toBeVisible();
    await expect(shortcuts.getByRole('button',{name:'我改过的发型预设',exact:true})).toBeVisible();
    await shortcuts.getByRole('button',{name:'推荐人物卡',exact:true}).click();
    await expect(page.locator('#chat-input')).toHaveValue(/^从本地资源中推荐几个人物外观包/);
    await go('profiles','启动方案');
    await expect(page.locator('.profile.is-default h3')).toContainText('基础收藏');
    await stop();
    expect(fs.readFileSync(path.join(data,'profiles.json'))).toEqual(profilesBefore);
    expect(fs.readFileSync(path.join(game,'AddonPackages/中文文件夹/作者.人物.1.var'))).toEqual(original);
    expect(fs.existsSync(path.join(game,'AddonPackages/Characters'))).toBe(false);
  }finally{
    await context.close().catch(()=>{});
    if(proc&&proc.exitCode===null)proc.kill();
    if(proc)await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
