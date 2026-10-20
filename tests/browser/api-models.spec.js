const {test,expect}=require('@playwright/test'),fs=require('fs');

test('API model combobox matches partial names and preserves custom IDs without changing settings layout',async({page,request})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8')),headers={'X-Session-Token':token};
  const before=await(await request.get(url+'/api/settings',{headers})).json(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let fail=false,release=null;
  const rows=[
    {id:'deepseek-ai/DeepSeek-V4.1-Flash',name:'DeepSeek Flash',tags:['能力未声明']},
    {id:'Qwen/Qwen3',name:'通用语言模型',tags:['仅文字']},
    {id:'org/vision',name:'视觉对话',tags:['识图']}
  ];
  await page.route('**/api/settings',async route=>{
    if(route.request().method()!=='GET')return route.continue();
    const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),has_key:true}});
  });
  await page.route('**/api/ai-models?provider=api*',async route=>{
    if(release)await new Promise(resolve=>{release=resolve;});
    if(fail)return route.fulfill({status:502,json:{error:'模型列表暂不可用'}});
    await route.fulfill({json:{models:rows,cached:false}});
  });
  const save=async()=>{
    const form=await page.locator('#settings-form').elementHandle();
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await expect.poll(()=>form.evaluate(el=>el.isConnected)).toBe(false);
  };
  try{
    await request.post(url+'/api/settings',{headers,data:{provider:'compatible',base_url:'https://api.siliconflow.cn/v1'}});
    await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('3 个模型');
    const chat=page.locator('[name=api_model]'),summary=page.locator('[name=api_summary_model]');
    const list=page.locator('#api_model-options');
    await chat.fill('dEePsEeK-v4.1');
    await expect(list.getByRole('option')).toHaveCount(1);
    await expect(list).toContainText(rows[0].id);
    await chat.press('ArrowDown');await chat.press('Enter');
    await expect(chat).toHaveValue(rows[0].id);
    await summary.fill('视觉');await page.locator('#api_summary_model-options [role=option]').click();
    await expect(summary).toHaveValue('org/vision');
    await expect(page.locator('[data-model-tags=api_summary_model]')).toContainText('识图');
    await page.getByRole('button',{name:'展开对话模型列表',exact:true}).click();
    await expect(list.getByRole('option')).toHaveCount(3);
    await chat.press('Escape');await expect(list).toBeHidden();
    await save();
    let settings=await(await request.get(url+'/api/settings',{headers})).json();
    expect(settings.api_model).toBe(rows[0].id);expect(settings.api_summary_model).toBe('org/vision');
    await chat.fill('private/Not-In-Catalog:latest');
    await expect(list).toContainText('未匹配');
    await chat.press('Enter');await expect(chat).toHaveValue('private/Not-In-Catalog:latest');
    await page.getByRole('button',{name:'刷新 API 模型',exact:true}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('3 个模型');
    await expect(chat).toHaveValue('private/Not-In-Catalog:latest');
    fail=true;await page.getByRole('button',{name:'刷新 API 模型',exact:true}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('仍可直接填写');
    await save();await expect(chat).toHaveValue('private/Not-In-Catalog:latest');
    settings=await(await request.get(url+'/api/settings',{headers})).json();
    expect(settings.api_model).toBe('private/Not-In-Catalog:latest');
    // An in-flight old endpoint response must not repopulate a changed connection.
    fail=false;release=true;
    await page.getByRole('button',{name:'刷新 API 模型',exact:true}).click();
    await expect.poll(()=>typeof release).toBe('function');
    await page.locator('[name=base_url]').fill('https://other.example/v1');
    release();release=null;
    await expect(page.getByRole('button',{name:'刷新 API 模型',exact:true})).toBeEnabled();
    await chat.fill('deepseek');await expect(list.getByRole('option')).toHaveCount(0);
    await page.getByRole('button',{name:'刷新 API 模型',exact:true}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('先保存');
    await page.locator('[name=base_url]').fill('https://api.siliconflow.cn/v1');
    await page.locator('[name=api_key]').fill('unsaved-fixture-key');
    await page.getByRole('button',{name:'刷新 API 模型',exact:true}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('先保存');
    await page.locator('[name=api_key]').fill('');
    await page.getByRole('button',{name:'刷新 API 模型',exact:true}).click();
    await expect(page.locator('[data-model-status=api]')).toContainText('3 个模型');
    await page.setViewportSize({width:390,height:700});
    await chat.fill('');await expect(list.getByRole('option')).toHaveCount(3);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.setViewportSize({width:1440,height:1000});
    await chat.fill('deepseek');
    await page.locator('#api-settings').screenshot({path:'test-results/API-模型输入匹配.png'});
    expect(errors).toEqual([]);
  }finally{
    if(typeof release==='function')release();
    await request.post(url+'/api/settings',{headers,data:before});
  }
});
