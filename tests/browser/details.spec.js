const {test,expect}=require('@playwright/test');
const fs=require('fs');
const path=require('path');
const connection=()=>JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));

test('whole list rows and grid covers open details without stealing checkbox or favorite clicks',async({page})=>{
  const {url,token}=connection();await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
  await page.getByLabel('搜索资源').fill('Luna');
  const row=page.locator('.package-row'),drawer=page.locator('#drawer-backdrop');
  await expect(row).toHaveCount(1);
  await row.locator('input[type=checkbox]').check();await expect(drawer).toBeHidden();
  await row.locator('.package-summary').click();await expect(drawer).toBeVisible();
  await page.getByRole('button',{name:'关闭详情',exact:true}).click();
  await row.focus();await page.keyboard.press('Enter');await expect(drawer).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
  await row.locator('.package-art').click();await expect(drawer).toBeVisible();
  await page.getByRole('button',{name:'关闭详情',exact:true}).click();
  // Verify the child action without moving even fixture files.
  let favoriteCalls=0;
  await page.route('**/api/favorite',route=>{favoriteCalls++;return route.fulfill({status:400,json:{error:'收藏按钮独立触发'}});});
  await row.locator('[data-favorite]').click();
  await expect(page.locator('#toast')).toHaveText('收藏按钮独立触发');expect(favoriteCalls).toBe(1);
  await expect(drawer).toBeHidden();
});

test('portrait and landscape previews fit entirely and a new detail resets scrolling',async({page})=>{
  const {url,token}=connection();await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
  await page.getByLabel('搜索资源').fill('Luna');
  await expect(page.locator('.package-row')).toHaveCount(1);
  await page.locator('.package-row .package-summary').click();
  const img=page.locator('#drawer .detail-cover img');await expect(img).toBeVisible();
  const check=()=>img.evaluate(el=>{
    const a=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();
    return {fit:getComputedStyle(el).objectFit,inside:a.top>=b.top&&a.bottom<=b.bottom&&a.left>=b.left&&a.right<=b.right,ratio:a.width/a.height,natural:el.naturalWidth/el.naturalHeight};
  });
  let result=await check();expect(result.fit).toBe('contain');expect(result.inside).toBe(true);expect(result.ratio).toBeCloseTo(result.natural);
  await page.screenshot({path:'test-results/详情-完整竖图.png'});
  // Replace only the test image source to exercise a landscape aspect ratio.
  await img.evaluate(async el=>{
    const canvas=document.createElement('canvas');canvas.width=900;canvas.height=300;
    const ctx=canvas.getContext('2d');ctx.fillStyle='#4b83ad';ctx.fillRect(0,0,900,300);ctx.fillStyle='#fff';ctx.fillRect(0,0,25,300);ctx.fillRect(875,0,25,300);
    el.src=canvas.toDataURL();await el.decode();
  });
  result=await check();expect(result.inside).toBe(true);expect(result.ratio).toBeCloseTo(3);
  await page.setViewportSize({width:390,height:844});result=await check();expect(result.inside).toBe(true);
  await page.locator('#drawer').evaluate(el=>{el.scrollTop=el.scrollHeight;});
  await page.keyboard.press('Escape');await page.locator('.package-row .package-summary').click();
  expect(await page.locator('#drawer').evaluate(el=>el.scrollTop)).toBe(0);
  await expect(img).toBeVisible();
});

test('closing a loading detail prevents late responses from reopening it',async({page})=>{
  const {url,token}=connection();await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
  await page.getByLabel('搜索资源').fill('Luna');
  await expect(page.locator('.package-row')).toHaveCount(1);
  await page.route('**/api/package?*',async route=>{const response=await route.fetch();await new Promise(r=>setTimeout(r,200));await route.fulfill({response});});
  const response=page.waitForResponse(r=>r.url().includes('/api/package?'));
  await page.locator('.package-row .package-summary').click();await page.keyboard.press('Escape');
  await response;await expect(page.locator('#drawer-backdrop')).toBeHidden();
});

test('summary image settings save independently of model choices',async({page,request})=>{
  const {url,token}=connection(),headers={'X-Session-Token':token};
  await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
  await expect(page.getByLabel('生成简介时参考包内封面')).toBeChecked();
  await page.getByLabel('生成简介时参考包内封面').uncheck();
  await page.getByRole('button',{name:'保存设置',exact:true}).click();
  let settings=await (await request.get(url+'/api/settings',{headers})).json();expect(settings.summary_images).toBe(false);expect(settings.codex_summary_model).toBe('gpt-6-luna');
  await page.locator('#ai-provider').selectOption('compatible');
  await page.getByLabel('生成简介时参考包内封面').check();await page.getByRole('button',{name:'保存设置',exact:true}).click();
  settings=await (await request.get(url+'/api/settings',{headers})).json();expect(settings.api_vision).toBeUndefined();expect(settings.summary_images).toBe(true);
  await request.post(url+'/api/settings',{headers,data:{provider:'codex',summary_images:true}});
});

test('model lists include hidden entries, tag capabilities and preserve selections on refresh',async({page,request})=>{
  const {url,token}=connection(),headers={'X-Session-Token':token};
  await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
  const summary=page.locator('[name=codex_summary_model]');
  await expect(summary.locator('option')).toHaveCount(5);
  await expect(summary.locator('option[value=hidden-model]')).toContainText('CLI 隐藏');
  await summary.selectOption('gpt-6-astra');
  await page.getByRole('button',{name:'刷新 Codex 模型'}).click();
  await expect(summary).toHaveValue('gpt-6-astra');
  await expect(page.locator('[data-model-tags=codex_summary_model]')).toContainText('识图');
  await page.locator('#ai-provider').selectOption('deepseek');
  await page.getByRole('button',{name:'刷新 API 模型'}).click();
  const model=page.locator('[name=api_model]');
  await model.selectOption('deepseek-v4-pro');
  await expect(page.locator('[data-model-tags=api_model]')).toContainText('仅文字');
  await model.selectOption('deepseek-flash');
  await expect(page.locator('[data-model-tags=api_model]')).toContainText('识图');
  const apiSummary=page.locator('[name=api_summary_model]');
  await apiSummary.selectOption('deepseek-v4-pro');
  await expect(page.locator('[data-model-tags=api_summary_model]')).toContainText('仅文字');
  await page.getByRole('button',{name:'刷新 API 模型'}).click();
  await expect(model).toHaveValue('deepseek-flash');await expect(apiSummary).toHaveValue('deepseek-v4-pro');
  const previousForm=await page.locator('#settings-form').elementHandle();
  await page.getByRole('button',{name:'保存设置',exact:true}).click();
  await expect.poll(()=>previousForm.evaluate(el=>el.isConnected)).toBe(false);
  await expect(summary).toHaveValue('gpt-6-astra');
  const settings=await (await request.get(url+'/api/settings',{headers})).json();
  expect(settings.api_model).toBe('deepseek-flash');expect(settings.api_summary_model).toBe('deepseek-v4-pro');expect(settings.secret).toBeUndefined();
  await page.locator('#ai-provider').selectOption('compatible');
  await expect(model).toBeVisible();await expect(apiSummary).toBeVisible();
  await expect(model).toHaveJSProperty('tagName','INPUT');await expect(apiSummary).toHaveJSProperty('tagName','INPUT');
  await page.locator('#ai-provider').selectOption('deepseek');
  await expect(model).toHaveValue('deepseek-flash');await expect(apiSummary).toHaveValue('deepseek-v4-pro');
  await page.screenshot({path:'test-results/API-对话与简介模型分离.png',fullPage:true});
  await request.post(url+'/api/settings',{headers,data:{provider:'codex',codex_summary_model:'gpt-6-luna',api_summary_model:'deepseek-flash'}});
});

test('long summaries remain editable and persist in full after reopening details',async({page,request})=>{
  const {url,token,fixtureAddon}=connection(),headers={'X-Session-Token':token};
  const file=path.join(fixtureAddon,'Long.Summary.1.var');
  const scan=async()=>{
    const job=await(await request.post(url+'/api/scan',{headers,data:{}})).json();
    await expect.poll(async()=>(await(await request.get(url+'/api/job?id='+job.id,{headers})).json()).status).toBe('done');
  };
  fs.copyFileSync('test-results/Multi.Bundle.1.var',file);
  try{
    await scan();await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();await page.getByLabel('搜索资源').fill('Long.Summary');
    await expect(page.locator('.package-row')).toHaveCount(1);await page.locator('.package-row').click();
    await page.locator('#drawer summary').filter({hasText:'编辑分类、简介与标签'}).click();
    const input=page.locator('#label-form [name=summary]');
    expect(await input.getAttribute('maxlength')).toBeNull();
    const summary=('人物外观简介，记录性别呈现、年龄观感与可见外貌特征。'.repeat(100))+'完整结尾不能被截断。';
    await input.fill(summary);
    const saved=page.waitForResponse(r=>r.url()===url+'/api/label'&&r.request().method()==='POST');
    await page.locator('#label-form').getByRole('button',{name:'保存修改',exact:true}).click();
    expect((await saved).ok()).toBe(true);
    await page.reload();await page.locator('#navigation [data-page="library"]').click();await page.getByLabel('搜索资源').fill('Long.Summary');
    await expect(page.locator('.package-row')).toHaveCount(1);await page.locator('.package-row').click();
    await expect(page.locator('#drawer')).toContainText(summary);
    await page.locator('#drawer summary').filter({hasText:'编辑分类、简介与标签'}).click();
    await expect(page.locator('#label-form [name=summary]')).toHaveValue(summary);
  }finally{fs.rmSync(file,{force:true});await scan();}
});
