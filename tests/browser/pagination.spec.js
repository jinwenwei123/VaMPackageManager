const {test,expect}=require('@playwright/test');
const fs=require('fs');
const connection=()=>JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
async function preferences(request,data){
  const {url,token}=connection(),headers={'X-Session-Token':token};
  return (await (data?request.post(url+'/api/library-preferences',{headers,data}):request.get(url+'/api/library-preferences',{headers}))).json();
}
test.beforeEach(async({request})=>{await preferences(request,{view:'list',sort:'name'});});
test.afterEach(async({request})=>{await preferences(request,{view:'list',sort:'name'});});

test('view and sort survive fresh browsers while status remains transient',async({page,browser,request})=>{
  const {url,token}=connection();await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
  await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
  await page.getByLabel('排序',{exact:true}).selectOption('size');
  await page.getByLabel('筛选状态').selectOption('active');
  await expect.poll(()=>preferences(request)).toEqual({view:'grid',sort:'size'});
  await page.locator('#navigation [data-action=favorites]').click();
  await expect(page.getByLabel('排序',{exact:true})).toHaveValue('size');
  await page.getByLabel('VaM 包管家首页').click();
  await expect(page.getByRole('button',{name:'⊞ 大图',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.getByLabel('排序',{exact:true})).toHaveValue('size');
  await page.getByLabel('筛选状态').selectOption('active');
  const context=await browser.newContext(),other=await context.newPage();
  await other.goto(url+'/#'+token);await other.locator('#navigation [data-page="library"]').click();
  await expect(other.getByLabel('排序',{exact:true})).toHaveValue('size');
  await expect(other.getByRole('button',{name:'⊞ 大图',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(other.getByLabel('筛选状态')).toHaveValue('');
  await page.reload();await page.locator('#navigation [data-page="library"]').click();
  await expect(page.getByLabel('排序',{exact:true})).toHaveValue('size');
  await expect(page.getByLabel('筛选状态')).toHaveValue('');
  await context.close();
});

test('glass pager floats, docks, jumps and fits narrow grid without duplicate controls',async({page,request})=>{
  const {url,token}=connection(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const seed=(await (await request.get(url+'/api/packages?q=Luna',{headers:{'X-Session-Token':token}})).json()).items[0];
  await page.route('**/api/packages?*',async route=>{
    const q=new URL(route.request().url()).searchParams,total=q.get('q')?1:95,limit=Number(q.get('limit')||40);
    const current=Math.min(Math.ceil(total/limit),Math.max(1,Number(q.get('page')||1)));
    const start=(current-1)*limit;
    await route.fulfill({json:{items:Array.from({length:Math.min(limit,total-start)},(_,i)=>({...seed,key:'fixture.item'+(start+i)+'.1',name:'资源 '+(start+i+1),favorite:false})),page:current,total,limit,
      summary:{packages:total,files:total,active:total,duplicates:0,bytes:12345,errors:0},facets:{total,categories:{人物:total}}}});
  });
  await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
  const bar=page.locator('[data-floating-pager] .pagination'),slot=page.locator('[data-floating-pager]');
  const first=bar.getByRole('button',{name:'第一页',exact:true}),last=bar.getByRole('button',{name:'最后一页',exact:true});
  const jump=bar.getByLabel('跳转页码');
  await expect(page.locator('.package-row')).toHaveCount(42);
  await expect(bar).toHaveClass(/is-floating/);
  await expect(bar.getByRole('button')).toHaveCount(4);
  await expect(first).toBeDisabled();
  const floating=await bar.evaluate(el=>({bg:getComputedStyle(el).backgroundColor,blur:getComputedStyle(el).backdropFilter,bottom:innerHeight-el.getBoundingClientRect().bottom}));
  expect(floating.bg).toContain('0.76');expect(floating.blur).toContain('blur(12px)');expect(floating.bottom).toBeCloseTo(18);
  await page.screenshot({path:'test-results/分页-悬浮磨砂.png'});
  await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
  await expect(bar).not.toHaveClass(/is-floating/);
  const dock=await bar.boundingBox(),reserved=await slot.boundingBox();
  expect(Math.abs(dock.y-reserved.y)).toBeLessThan(1);
  await page.screenshot({path:'test-results/分页-底部归位.png'});
  await last.click();await expect(slot).toHaveAttribute('data-current','3');await expect(last).toBeDisabled();
  await expect(page.locator('.package-row')).toHaveCount(11);
  await first.click();await expect(slot).toHaveAttribute('data-current','1');
  await jump.fill('2');await jump.press('Enter');await expect(slot).toHaveAttribute('data-current','2');
  expect((await page.locator('.package-row').first().boundingBox()).y).toBeLessThan(150);
  await bar.getByRole('button',{name:'上一页',exact:true}).click();await expect(jump).toHaveValue('1');
  await bar.getByRole('button',{name:'下一页',exact:true}).click();await expect(jump).toHaveValue('2');
  await jump.fill('999');await jump.press('Enter');await expect(jump).toHaveValue('3');
  await jump.fill('abc');await jump.press('Enter');await expect(page.locator('#toast')).toContainText('请输入 1～3');
  await jump.fill('0');await jump.press('Enter');await expect(jump).toHaveValue('1');
  await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
  await expect(page.locator('.package-row')).toHaveCount(42);
  await page.setViewportSize({width:390,height:844});
  await expect(bar).toHaveClass(/is-floating/);
  await expect.poll(async()=>{const b=await bar.boundingBox();return b.x+b.width;}).toBeLessThanOrEqual(390);
  const mobile=await bar.boundingBox();expect(mobile.x).toBeGreaterThanOrEqual(0);expect(mobile.x+mobile.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:'test-results/分页-窄窗口.png'});
  await page.getByLabel('搜索资源').fill('only one');
  await expect(slot).toHaveAttribute('data-count','1');await expect(bar).not.toHaveClass(/is-floating/);
  await expect(jump).toBeDisabled();
  await page.getByRole('button',{name:'⚙ AI 与设置'}).click();await expect(bar).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('dependency pages use the same first, last and jump controls',async({page})=>{
  const {url,token}=connection();
  await page.route('**/api/missing?*',route=>{
    const q=new URL(route.request().url()).searchParams,total=q.get('q')?1:161,page=Math.min(Math.ceil(total/80),Math.max(1,Number(q.get('page')||1)));
    const start=(page-1)*80;
    return route.fulfill({json:{page,total,items:Array.from({length:Math.min(80,total-start)},(_,i)=>({ref:'Asset.Missing'+(start+i)+'.1',users:['Scene'],count:1}))}});
  });
  await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⛓ 依赖检查'}).click();
  const bar=page.locator('[data-floating-pager] .pagination'),jump=bar.getByLabel('跳转页码');
  await expect(bar).toHaveClass(/is-floating/);
  await bar.getByRole('button',{name:'最后一页',exact:true}).click();await expect(jump).toHaveValue('3');
  await jump.fill('2');await jump.press('Enter');await expect(jump).toHaveValue('2');
  await page.getByLabel('搜索缺失依赖').fill('single');await expect(jump).toHaveValue('1');await expect(jump).toBeDisabled();
});
