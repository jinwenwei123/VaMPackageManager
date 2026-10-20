const {test,expect}=require('@playwright/test'),fs=require('fs');

test('switching back to DeepSeek restores its own controls and keeps compatible API drafts separate',async({page,request})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8')),headers={'X-Session-Token':token};
  const before=await(await request.get(url+'/api/settings',{headers})).json();
  const save=async()=>{
    const form=await page.locator('#settings-form').elementHandle();
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await expect.poll(()=>form.evaluate(el=>el.isConnected)).toBe(false);
  };
  try{
    await request.post(url+'/api/settings',{headers,data:{provider:'compatible',base_url:'https://api.siliconflow.cn/v1',
      api_model:'private/chat',api_summary_model:'private/summary',api_context_window:131072}});
    await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    const provider=page.locator('#ai-provider'),chat=page.locator('[name=api_model]'),summary=page.locator('[name=api_summary_model]');
    const base=page.locator('[name=base_url]'),key=page.locator('[name=api_key]');
    await chat.fill('private/draft');await key.fill('unsaved-compatible-fixture');
    await page.locator('[name=clear_key]').check();
    await provider.selectOption('deepseek');
    await expect(base).toHaveValue('https://api.deepseek.com');
    await expect(chat).toHaveJSProperty('tagName','SELECT');
    await expect(chat).toHaveValue('deepseek-flash');
    await expect(summary).toHaveValue('deepseek-flash');
    await expect(key).toHaveValue('');
    await expect(page.locator('[name=clear_key]')).not.toBeChecked();
    expect((await(await request.get(url+'/api/settings',{headers})).json()).provider).toBe('compatible');
    await expect(page.locator('[name=api_context_window]')).toHaveValue('0');
    await chat.selectOption('deepseek-v4-pro');
    await provider.selectOption('compatible');
    await expect(chat).toHaveJSProperty('tagName','INPUT');
    await expect(chat).toHaveValue('private/draft');
    await expect(summary).toHaveValue('private/summary');
    await expect(base).toHaveValue('https://api.siliconflow.cn/v1');
    await expect(key).toHaveValue('unsaved-compatible-fixture');await key.fill('');
    await expect(page.locator('[name=clear_key]')).toBeChecked();await page.locator('[name=clear_key]').uncheck();
    await save();await expect(chat).toHaveValue('private/draft');
    await provider.selectOption('deepseek');await chat.selectOption('deepseek-v4-pro');await save();
    await page.reload();await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    await expect(chat).toHaveJSProperty('tagName','SELECT');await expect(chat).toHaveValue('deepseek-v4-pro');
    await provider.selectOption('compatible');await expect(chat).toHaveValue('private/draft');
    await expect(base).toHaveValue('https://api.siliconflow.cn/v1');
    await provider.selectOption('codex');await provider.selectOption('deepseek');
    await expect(chat).toHaveValue('deepseek-v4-pro');
    await page.locator('#api-settings').screenshot({path:'test-results/DeepSeek-专用界面恢复.png'});
  }finally{await request.post(url+'/api/settings',{headers,data:before});}
});

test('a late DeepSeek model response cannot replace the compatible service list',async({page,request})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8')),headers={'X-Session-Token':token};
  const before=await(await request.get(url+'/api/settings',{headers})).json();
  let release=null;
  await page.route('**/api/settings',async route=>{
    if(route.request().method()!=='GET')return route.continue();
    const response=await route.fetch(),cfg=await response.json();
    cfg.has_key=true;for(const profile of Object.values(cfg.api_profiles))profile.has_key=true;
    await route.fulfill({response,json:cfg});
  });
  await page.route('**/api/ai-models?provider=api*',async route=>{
    const official=new URL(route.request().url()).searchParams.get('service')==='deepseek';
    if(official)await new Promise(resolve=>{release=resolve;});
    const id=official?'deepseek-flash':'private/model';
    await route.fulfill({json:{models:[{id,name:id,tags:['仅文字']}]}});
  });
  try{
    await request.post(url+'/api/settings',{headers,data:{provider:'compatible',base_url:'https://relay.example/v1',
      api_model:'private/model',api_summary_model:'private/model'}});
    await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('已读取');
    await page.locator('#ai-provider').selectOption('deepseek');
    await expect.poll(()=>typeof release).toBe('function');
    await page.locator('#ai-provider').selectOption('compatible');
    await expect(page.locator('[data-model-status=api]')).toContainText('已读取');
    const lateResponse=page.waitForResponse(response=>response.url().includes('service=deepseek'));
    release();release=null;
    await(await lateResponse).finished();
    await page.evaluate(()=>new Promise(requestAnimationFrame));
    const chat=page.locator('[name=api_model]');
    await chat.fill('deepseek');await expect(page.locator('#api_model-options [role=option]')).toHaveCount(0);
    await chat.fill('private');await expect(page.locator('#api_model-options [role=option]')).toHaveCount(1);
    await expect(page.locator('#api_model-options')).toContainText('private/model');
  }finally{
    if(release)release();
    await request.post(url+'/api/settings',{headers,data:before});
  }
});
