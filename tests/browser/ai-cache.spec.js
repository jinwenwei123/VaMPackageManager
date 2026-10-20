const {test,expect}=require('@playwright/test');
const fs=require('fs');

test('API usage shows reported weighted cache rate and updates while settings stay open',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  let hit=180;
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/status',async route=>{
    const response=await route.fetch();
    await route.fulfill({response,json:{...await response.json(),ai_usage:[{
      provider:'deepseek',model:'fixture-model',purpose:'generation',requests:3,
      prompt_tokens:6000,completion_tokens:30,prompt_cache_hit_tokens:hit,cache_observed_tokens:1000,
      last:{prompt_cache_hit_tokens:90,cache_observed_tokens:900}
    },{provider:'compatible',model:'unreported',purpose:'chat',requests:1,prompt_tokens:200}]}});
  });
  await page.goto(url+'/#'+token);
  await page.getByRole('button',{name:/AI 与设置/}).click();
  await expect(page.locator('#ai-usage')).toContainText('累计缓存命中 18.0%');
  await expect(page.locator('#ai-usage')).toContainText('最近请求 10.0%');
  await expect(page.locator('#ai-usage')).toContainText('服务未报告');
  hit=400;
  await expect(page.locator('#ai-usage')).toContainText('累计缓存命中 40.0%');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  expect(errors).toEqual([]);
});

test('manual API context capacity persists and automatic mode can be restored',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token};
  const before=await(await page.request.get(url+'/api/settings',{headers})).json();
  try{
    await page.goto(url+'/#'+token);
    await page.getByRole('button',{name:/AI 与设置/}).click();
    await page.locator('#ai-provider').selectOption('compatible');
    await page.locator('#api-settings summary').filter({hasText:'上下文容量设置'}).click();
    const capacity=page.locator('[name=api_context_window]');
    await expect(capacity).toHaveValue(String(before.api_context_window||0));
    await capacity.fill('32768');
    const previous=await page.locator('#settings-form').elementHandle();
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await expect.poll(()=>previous.evaluate(el=>el.isConnected)).toBe(false);
    const saved=await(await page.request.get(url+'/api/settings',{headers})).json();
    expect(saved.api_context_window).toBe(32768);
    await page.reload();await page.getByRole('button',{name:/AI 与设置/}).click();
    await page.locator('#api-settings summary').filter({hasText:'上下文容量设置'}).click();
    await expect(page.locator('[name=api_context_window]')).toHaveValue('32768');
  }finally{
    await page.request.post(url+'/api/settings',{headers,data:{...before,api_context_window:before.api_context_window||0}});
  }
});
