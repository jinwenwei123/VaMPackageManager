const {test,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

test('overlapping views, fixed base UI and reversible single-file layout',async({page})=>{
  const {url,token,fixtureAddon,fixtureVault}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token},errors=[];page.on('pageerror',e=>errors.push(e.message));
  const get=async route=>(await page.request.get(url+'/api/'+route,{headers})).json();
  const done=async job=>{await expect.poll(async()=>(await get('job?id='+job.id)).status).toBe('done');return (await get('job?id='+job.id)).result;};
  const post=async(route,data)=>{const response=await page.request.post(url+'/api/'+route,{headers,data});expect(response.ok()).toBe(true);const out=await response.json();return out.status==='running'?done(out):out;};
  let source=path.join(fixtureAddon,'Multi.Bundle.1.var');fs.copyFileSync('test-results/Multi.Bundle.1.var',source);
  const before=hash(source);await post('scan',{});
  const scanned=(await get('package?key=multi.bundle.1')).files[0];
  source=path.join(scanned.area==='active'?fixtureAddon:fixtureVault,scanned.rel);
  await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();await page.getByLabel('搜索资源').fill('Multi.Bundle');
  await expect(page.locator('.package-row')).toHaveCount(1);
  for(const cat of ['场景','人物','服饰']){
    await expect(page.locator(`[data-category="${cat}"] small`)).toHaveText('1');
    await page.locator(`[data-category="${cat}"]`).click();await expect(page.locator('.package-row')).toHaveCount(1);
  }
  await page.locator('.package-row').click();
  await expect(page.locator('#drawer .package-category')).toHaveCount(3);
  await page.locator('[data-action=detail-pin]').click();
  await expect(page.locator('#dialog')).toContainText('固定基础包');
  const response=page.waitForResponse(r=>r.url()===url+'/api/apply'&&r.request().method()==='POST');
  await page.locator('[data-action=apply-plan]').click();
  const applied=await done(await(await response).json());
  const pkg=await get('package?key=multi.bundle.1');
  expect(pkg.files).toHaveLength(1);expect(pkg.files[0].rel).toBe('基础包/Multi.Bundle.1.var');
  expect(hash(path.join(fixtureAddon,pkg.files[0].rel))).toBe(before);expect(fs.existsSync(source)).toBe(false);
  await expect(page.locator('#drawer')).toContainText('固定基础包 · 常驻启用');
  await page.getByRole('button',{name:'关闭详情'}).click();
  await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
  await page.getByRole('button',{name:'管理固定基础包'}).click();
  await expect(page.getByLabel('筛选状态')).toHaveValue('pinned');
  await expect(page.locator('.pin-mark')).toHaveCount(2);
  const organize=await post('plan',{kind:'organize',keys:['multi.bundle.1']});expect(organize.count).toBe(0);
  await post('favorite',{keys:['multi.bundle.1'],favorite:true});
  expect((await get('package?key=multi.bundle.1')).files[0].rel).toBe('基础包/Multi.Bundle.1.var');
  await page.screenshot({path:'test-results/交叉分类-固定基础包.png'});
  const recent=(await get('history')).find(x=>x.kind==='favorite'&&x.status==='done');await post('undo',{id:recent.id});
  await post('undo',{id:applied.operation});expect(hash(source)).toBe(before);
  fs.unlinkSync(source);await post('scan',{});expect(errors).toEqual([]);
});

test('selection toolbar floats with glass and returns to its original slot on scroll',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const seed=(await(await page.request.get(url+'/api/packages?limit=1',{headers:{'X-Session-Token':token}})).json()).items[0];
  await page.route('**/api/packages?*',route=>route.fulfill({json:{items:Array.from({length:40},(_,i)=>({...seed,key:'layout.item'+i+'.1',name:'测试资源 '+i})),page:1,total:40,
    summary:{packages:40,files:40,active:40,duplicates:0,bytes:4000,errors:0},facets:{total:40,categories:{场景:40}}}}));
  await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
  await page.locator('.package-row input[type=checkbox]').first().check();
  const bar=page.locator('#selection-bar'),slot=page.locator('#selection-slot');
  await expect(bar).toBeVisible();await expect(bar).not.toHaveClass(/is-floating/);
  await page.evaluate(()=>window.scrollTo(0,1200));await expect(bar).toHaveClass(/is-floating/);
  expect(await bar.evaluate(e=>getComputedStyle(e).backdropFilter)).toContain('blur(18px)');
  expect((await bar.boundingBox()).y).toBeCloseTo(12);
  await page.screenshot({path:'test-results/交叉分类-顶部悬浮选择.png'});
  await page.setViewportSize({width:420,height:844});
  await expect.poll(async()=>{const b=await bar.boundingBox();return b.x+b.width;}).toBeLessThanOrEqual(420);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/交叉分类-选择工具栏窄屏.png'});
  await page.evaluate(()=>window.scrollTo(0,0));await expect(bar).not.toHaveClass(/is-floating/);
  expect(Math.abs((await bar.boundingBox()).y-(await slot.boundingBox()).y)).toBeLessThan(1);
  await bar.getByRole('button',{name:'清空',exact:true}).click();await expect(slot).toBeHidden();
});
