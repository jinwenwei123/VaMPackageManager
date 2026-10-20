const {test,expect}=require('@playwright/test');
const fs=require('fs');
const connection=()=>JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
const card=(page,key)=>page.locator(`[data-home-card="${key}"]`);
const home=async page=>{await page.locator('#navigation [data-page=home]').click();await expect(page.locator('.home-card')).toHaveCount(10);};

test('home is the first landing entry, uses real counts, links and resource editing',async({page})=>{
  const {url,token}=connection(),headers={'X-Session-Token':token},errors=[],writes=[];
  const get=async path=>(await page.request.get(url+'/api/'+path,{headers})).json();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST'&&!r.url().includes('/api/page/'))writes.push(r.url());});
  await page.goto(url+'/#'+token);
  await expect(page.getByRole('heading',{name:'主页',exact:true})).toBeVisible();
  await expect(page.locator('#navigation>button').first()).toHaveAttribute('data-page','home');
  await expect(page.locator('#navigation>button').nth(1)).toHaveAttribute('data-action','favorites');
  await expect(page.locator('.home-card')).toHaveCount(10);
  const {stats}=await get('status');
  await expect(card(page,'library').locator('.home-total strong')).toHaveText(stats.packages.toLocaleString('zh-CN'));
  await expect(card(page,'favorites').locator('.home-total strong')).toHaveText(stats.favorites.toLocaleString('zh-CN'));
  expect(writes).toEqual([]);
  // Explicit search clears the previous scope and opens the actual result.
  await page.locator('#home-search-input').fill('Luna');
  await page.locator('#home-search-input').press('Enter');
  await expect(page.locator('.package-row')).toHaveCount(1);
  await expect(page.locator('.package-name button').first()).toHaveText('Luna');
  await page.locator('.brand').click();
  await expect(page.getByRole('heading',{name:'主页',exact:true})).toBeVisible();
  await card(page,'library').getByRole('button',{name:/已启用/}).click();
  await expect(page.getByLabel('筛选状态')).toHaveValue('active');
  await home(page);
  await card(page,'library').locator('[data-home-category]').first().click();
  await expect(page.locator('#library-categories .chip.active')).not.toContainText('全部');
  await home(page);
  for(const [key,title] of [['directories','AI 与设置'],['library','资源库'],['favorites','收藏夹'],['profiles','启动方案'],['service','AI 与设置'],['generation','资源库'],['health','依赖检查'],['usage','AI 与设置'],['appearance','AI 与设置'],['history','操作记录']]){
    await card(page,key).locator('.home-arrow').click();
    await expect(page.getByRole('heading',{name:title,exact:true})).toBeVisible();
    await home(page);
  }
  await card(page,'service').getByRole('button',{name:/对话模型/}).click();
  const config=await get('settings');
  await expect(page.locator(`#settings-form [name=${config.provider==='codex'?'codex_model':'api_model'}]`)).toBeFocused();
  await home(page);
  await card(page,'profiles').getByRole('button',{name:'从收藏 / 文件夹创建',exact:true}).click();
  await expect(page.locator('#profile-source-form')).toBeVisible();
  await page.locator('#dialog [data-action=close-modal]').click();
  // Put a harmless favorite in the fixture, then open and edit it from the home card.
  const luna=await get('package?key=artist.luna.2');
  await page.request.post(url+'/api/label',{headers,data:{key:luna.key,favorite:true}});
  await home(page);
  const favorite=card(page,'favorites').locator('[data-detail]').first(),key=await favorite.getAttribute('data-detail');
  const original=await get('package?key='+key);
  await favorite.click();
  await expect(page.locator('#drawer h2')).toHaveText(original.name);
  await page.locator('#drawer details').filter({hasText:'编辑分类、简介与标签'}).locator('summary').click();
  await page.locator('#label-form [name=summary]').fill('从主页查看并编辑简介');
  await page.locator('#label-form button[type=submit]').click();
  await expect.poll(async()=>(await get('package?key='+key)).summary).toBe('从主页查看并编辑简介');
  await page.request.post(url+'/api/label',{headers,data:{key,summary:original.summary}});
  await page.request.post(url+'/api/label',{headers,data:{key:luna.key,favorite:luna.favorite}});
  expect(errors).toEqual([]);
});

test('home themes persist, live updates preserve search, narrow layouts fit and cards fail independently',async({page})=>{
  const {url,token}=connection(),headers={'X-Session-Token':token};
  const appearance=(await page.request.get(url+'/api/appearance',{headers})).json();
  let generated=2;
  await page.route('**/api/status',async route=>{const response=await route.fetch(),data=await response.json();data.revision='home-fixture-'+generated;data.summary_queue={status:'running',target:'both',updated:generated,current:generated,total:10};await route.fulfill({json:data});});
  await page.route('**/api/history',route=>route.fulfill({status:500,json:{error:'fixture unavailable'}}));
  try{
    await page.goto(url+'/#'+token);
    await expect(card(page,'history')).toContainText('暂时无法读取');
    await expect(card(page,'library').locator('.home-total')).toBeVisible();
    await expect(card(page,'generation').locator('.home-generation-count strong')).toHaveText('2');
    const input=page.locator('#home-search-input');await input.fill('保留输入');
    generated=7;
    await expect(card(page,'generation').locator('.home-generation-count strong')).toHaveText('7',{timeout:10000});
    await expect(input).toBeFocused();await expect(input).toHaveValue('保留输入');
    await card(page,'appearance').getByRole('button',{name:'海洋蓝',exact:true}).click();
    await card(page,'appearance').locator('[data-mode-choice=dark]').click();
    await expect.poll(async()=>(await page.request.get(url+'/api/appearance',{headers})).json()).toEqual({palette:'ocean',mode:'dark'});
    await page.unroute('**/api/history');await page.unroute('**/api/status');
    await page.reload();await expect(card(page,'appearance').locator('[data-mode-choice=dark]')).toHaveAttribute('aria-pressed','true');
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:'test-results/主页-深色.png',fullPage:true});
    await card(page,'appearance').locator('[data-mode-choice=light]').click();
    await card(page,'appearance').getByRole('button',{name:'森林绿',exact:true}).click();
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:'test-results/主页-桌面.png',fullPage:true});
    for(const width of [1000,760,420]){
      await page.setViewportSize({width,height:900});
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    }
    await page.screenshot({path:'test-results/主页-窄屏.png',fullPage:true});
  }finally{await page.request.post(url+'/api/appearance',{headers,data:await appearance});}
});
