const manual=require('./manual_windows');
/* Run with Windows node.exe after starting VaMPackageManager.exe --no-browser. Read-only. */
const {chromium}=require('playwright');
const fs=require('fs');
(async()=>{
  const server=JSON.parse(fs.readFileSync('data/server.json','utf8'));
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1480,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(server.url+'/#'+server.token);
  await page.getByRole('heading',{name:'资源库',exact:true}).waitFor();
  await page.getByRole('button',{name:'✧ 一键 AI 简介',exact:true}).waitFor();
  await page.locator('.package-row>.package-category').first().waitFor();
  console.log('Windows EXE WebUI: library loaded; rows='+await page.locator('.package-row').count());
  const status=await (await page.request.get(server.url+'/api/status',{headers:{'X-Session-Token':server.token}})).json();
  if(status.stats.favorites){
    await page.locator('[data-action=favorites]').click();
    await page.getByRole('heading',{name:'收藏夹',exact:true}).waitFor();
    await page.getByRole('button',{name:'✧ 一键 AI 简介',exact:true}).waitFor();
    if(!(await page.locator('.page-head').innerText()).includes(status.stats.favorite_folder))throw Error('Wrong favorites folder');
    const favorites=await (await page.request.get(server.url+'/api/packages?scope=favorites&with_counts=1',{headers:{'X-Session-Token':server.token}})).json();
    if(favorites.total!==status.stats.favorites||favorites.summary.packages!==favorites.total)throw Error('Wrong favorites totals');
    if(await page.locator('[data-category=""] small').innerText()!==favorites.total.toLocaleString('zh-CN'))throw Error('Category tabs still show full library');
    if(await page.locator('#library-stats .stat-value').first().innerText()!==favorites.total.toLocaleString('zh-CN'))throw Error('Summary still shows full library');
    for(const [category,count] of Object.entries(favorites.facets.categories)){
      if(await page.locator(`[data-category="${category}"] small`).innerText()!==count.toLocaleString('zh-CN'))throw Error('Wrong category count: '+category);
    }
    await page.locator('.filters').screenshot({path:'test-results/Windows收藏分类计数.png'});
    await page.screenshot({path:'test-results/Windows一键AI简介-收藏列表.png',fullPage:false});
    await page.locator('#state-filter').selectOption('active');
    await page.getByRole('heading',{name:'收藏夹',exact:true}).waitFor();
    const active=await (await page.request.get(server.url+'/api/packages?scope=favorites&state=active&with_counts=1',{headers:{'X-Session-Token':server.token}})).json();
    if(await page.locator('[data-category=""] small').innerText()!==active.total.toLocaleString('zh-CN'))throw Error('Status filter escaped favorites');
    console.log('Favorite category counts verified: '+JSON.stringify(favorites.facets.categories));
    await page.getByRole('button',{name:'▷ 启动方案',exact:true}).click();
    await page.locator('.profile').first().waitFor();
    const profiles=await (await page.request.get(server.url+'/api/profiles',{headers:{'X-Session-Token':server.token}})).json();
    const dynamic=profiles.find(p=>p.source?.type==='favorites');
    if(dynamic){
      if(dynamic.keys.length!==status.stats.favorites)throw Error('Favorites profile is stale');
      const card=page.locator('.profile').filter({has:page.getByRole('heading',{name:dynamic.name+' 自动更新',exact:true})});
      await card.getByRole('button',{name:'复制为固定选择',exact:true}).waitFor();
      if(!(await card.innerText()).includes('无需重新保存'))throw Error('Missing automatic sync guidance');
    }
    await page.screenshot({path:'test-results/Windows收藏启动方案.png',fullPage:false});
    console.log('Real favorites and saved source profiles loaded; favorites='+status.stats.favorites);
    await page.locator('#navigation [data-page=library]').click();
  }
  await page.locator('[data-category="头发"]').click();
  await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
  await page.locator('.package-list.grid-view').waitFor();
  await page.locator('.package-art img').first().waitFor({timeout:15000});
  if(await page.locator('.package-row>.package-category').first().evaluate(el=>getComputedStyle(el).position)!=='absolute')throw Error('Missing grid category tag');
  await page.screenshot({path:'test-results/Windows真实封面大图.png',fullPage:false});
  console.log('Real VAR cover previews loaded='+await page.locator('.package-art img').count());
  await page.locator('[data-category=""]').click();
  await page.getByRole('button',{name:'☷ 列表',exact:true}).click();
  await page.getByLabel('搜索资源').fill('Timeline');
  await page.waitForTimeout(1000);
  console.log('Timeline search rows='+await page.locator('.package-row').count());
  await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
  await page.getByRole('heading',{name:'AI 与设置',exact:true}).waitFor();
  if(await page.locator('[name=codex_summary_model]').inputValue()!=='gpt-6-luna')throw Error('Wrong summary model');
  if(await page.locator('[name=codex_model]').inputValue()!=='gpt-6.1-sol')throw Error('Wrong chat model');
  const timings=await page.evaluate(async({token})=>{
    const result={};
    for(const route of ['status','packages?q=hair','packages?scope=favorites&with_counts=1','missing','sources','profiles']){
      const runs=[];
      for(let i=0;i<12;i++){
        const t=performance.now();const r=await fetch('/api/'+route,{headers:{'X-Session-Token':token}});await r.json();runs.push(performance.now()-t);
      }
      runs.sort((a,b)=>a-b);result[route]={median_ms:runs[6],max_ms:runs.at(-1)};
    }
    return result;
  },{token:server.token});
  await page.getByRole('button',{name:'✧ 对话助手'}).click();
  await page.getByRole('heading',{name:'对话助手',exact:true}).waitFor();
  await page.screenshot({path:'test-results/Windows对话助手.png',fullPage:false});
  fs.writeFileSync('test-results/windows-ui-performance.json',JSON.stringify(timings,null,2));
  console.log('Native browser + local HTTP timings: '+JSON.stringify(timings));
  console.log('Windows settings loaded; JS errors='+JSON.stringify(errors));
  if(errors.length)throw Error('Browser errors');
  await browser.close();
})();
