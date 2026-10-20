const {test,expect}=require('@playwright/test'),fs=require('fs');

test('capability sources and per-model corrections persist, reset and stay isolated by endpoint',async({page,request})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8')),headers={'X-Session-Token':token};
  const before=await(await request.get(url+'/api/settings',{headers})).json(),errors=[];
  const base='https://api.siliconflow.cn/v1',model='Qwen/Qwen2.5-VL-72B-Instruct';
  page.on('pageerror',e=>errors.push(e.message));
  const save=async()=>{
    const form=await page.locator('#settings-form').elementHandle();
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await expect.poll(()=>form.evaluate(el=>el.isConnected)).toBe(false);
  };
  try{
    await request.post(url+'/api/settings',{headers,data:{provider:'compatible',base_url:base,
      api_model:model,api_summary_model:model,api_capability_overrides:{}}});
    await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    const chat=page.locator('[name=api_model]'),summary=page.locator('[name=api_summary_model]');
    const editor=page.locator('[data-capability-editor=api_model]'),other=page.locator('[data-capability-editor=api_summary_model]');
    const tags=page.locator('[data-model-tags=api_model]'),otherTags=page.locator('[data-model-tags=api_summary_model]');
    await expect(tags).toContainText('识图');await expect(tags).toContainText('官方资料');
    await editor.locator('summary').click();
    await expect(editor.getByRole('link',{name:/查看依据/})).toHaveAttribute('href',/docs.siliconflow.cn/);
    await editor.getByLabel('对话模型识图能力').selectOption('false');
    await expect(tags).toContainText('仅文字');await expect(otherTags).toContainText('仅文字');
    await expect(tags).toContainText('手动设置');
    await editor.getByLabel('对话模型工具调用能力').selectOption('true');
    await save();await expect(tags).toContainText('仅文字');await expect(otherTags).toContainText('工具调用');
    let cfg=await(await request.get(url+'/api/settings',{headers})).json();
    expect(cfg.api_capability_overrides[model]).toEqual({vision:false,tool_calling:true});
    await editor.locator('summary').click();
    await editor.getByLabel('对话模型识图能力').selectOption('auto');
    await expect(tags).toContainText('官方资料');
    await expect(tags.locator('.badge').filter({hasText:'识图'})).toHaveText('识图');
    await save();cfg=await(await request.get(url+'/api/settings',{headers})).json();
    expect(cfg.api_capability_overrides[model]).toEqual({tool_calling:true});
    await chat.fill('private/custom');await chat.press('Escape');
    await expect(tags).not.toContainText('工具调用');
    await editor.locator('summary').click();
    await editor.getByLabel('对话模型识图能力').selectOption('true');
    await editor.getByLabel('对话模型JSON 输出能力').selectOption('true');
    await save();await expect(tags).toContainText('JSON 输出');
    await expect(summary).toHaveValue(model);await expect(otherTags).toContainText('官方资料');
    await editor.locator('summary').click();await other.locator('summary').click();
    await page.setViewportSize({width:390,height:850});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#api-settings').screenshot({path:'test-results/API-模型能力来源与设置.png'});
    await page.locator('[name=base_url]').fill('https://other.example/v1');
    await expect(editor.getByLabel('对话模型识图能力')).toBeDisabled();
    await expect(tags).not.toContainText('手动设置');
    await save();await expect(tags).not.toContainText('手动设置');
    cfg=await(await request.get(url+'/api/settings',{headers})).json();expect(cfg.api_capability_overrides).toEqual({});
    await page.locator('[name=base_url]').fill(base+'/chat/completions/');
    await save();await expect(tags).toContainText('JSON 输出');
    await expect(tags).toContainText('手动设置');
    expect(errors).toEqual([]);
  }finally{
    await request.post(url+'/api/settings',{headers,data:{base_url:base,api_capability_overrides:{}}});
    await request.post(url+'/api/settings',{headers,data:before});
  }
});
