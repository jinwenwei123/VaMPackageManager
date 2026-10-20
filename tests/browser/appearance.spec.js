const {test,expect}=require('@playwright/test');
const fs=require('fs');
const connection=()=>JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
async function saved(request){const {url,token}=connection();return (await request.get(url+'/api/appearance',{headers:{'X-Session-Token':token}})).json();}
test.beforeEach(async({request})=>{
  const {url,token}=connection();
  await request.post(url+'/api/appearance',{headers:{'X-Session-Token':token},data:{palette:'forest',mode:'light'}});
});
test.afterEach(async({request})=>{
  const {url,token}=connection();
  await request.post(url+'/api/appearance',{headers:{'X-Session-Token':token},data:{palette:'forest',mode:'light'}});
});

test('appearance persists without browser storage, supports system mode and syncs tabs',async({page,browser,request})=>{
  const {url,token}=connection(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url+'/#'+token);
  await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
  await page.getByRole('button',{name:'鸢尾紫'}).click();
  await page.locator('[data-mode-choice=dark]').click();
  await expect(page.locator('html')).toHaveAttribute('data-palette','violet');
  await expect(page.locator('html')).toHaveAttribute('data-mode','dark');
  await expect.poll(()=>saved(request)).toEqual({palette:'violet',mode:'dark'});
  await page.screenshot({path:'test-results/主题设置-深色鸢尾紫.png',fullPage:true});
  // A completely fresh browser context has no local/session storage to fall back to.
  const context=await browser.newContext(),other=await context.newPage();
  await other.goto(url+'/#'+token,{waitUntil:'domcontentloaded'});
  await expect(other.locator('html')).toHaveAttribute('data-mode','dark');
  await expect(other.locator('html')).toHaveAttribute('data-palette','violet');
  await other.getByLabel('切换浅色模式').click();
  await expect(page.locator('[data-mode-choice=light]')).toHaveAttribute('aria-pressed','true');
  await page.emulateMedia({colorScheme:'dark'});
  await page.locator('[data-mode-choice=system]').click();
  await expect(page.locator('html')).toHaveAttribute('data-mode','dark');
  await page.emulateMedia({colorScheme:'light'});
  await expect(page.locator('html')).toHaveAttribute('data-mode','light');
  await expect.poll(()=>saved(request)).toEqual({palette:'violet',mode:'system'});
  await context.close();
  expect(errors).toEqual([]);
});

test('all six palettes render both modes, preserve covers and fit narrow settings',async({page,request})=>{
  await page.emulateMedia({reducedMotion:'reduce'});
  const {url,token}=connection();await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
  await page.getByLabel('搜索资源').fill('Luna');
  await expect(page.locator('.package-row')).toHaveCount(1);
  await page.locator('.package-art img').waitFor();
  expect(await page.locator('.warning-mark').evaluate(el=>getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
  const cover=await page.locator('.package-art img').getAttribute('src');
  for(const palette of ['forest','ocean','violet','rose','amber','graphite']){
    for(const mode of ['light','dark']){
      await request.post(url+'/api/appearance',{headers:{'X-Session-Token':token},data:{palette,mode}});
      await expect(page.locator('html')).toHaveAttribute('data-palette',palette);
      await expect(page.locator('html')).toHaveAttribute('data-mode',mode);
      const colors=await page.evaluate(()=>{
        const rgb=s=>s.match(/[\d.]+/g).slice(0,3).map(Number);
        const luminance=s=>rgb(s).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((a,n,i)=>a+n*[.2126,.7152,.0722][i],0);
        const el=document.querySelector('.button.primary'),style=getComputedStyle(el);
        const [lo,hi]=[luminance(style.color),luminance(style.backgroundColor)].sort((a,b)=>a-b);
        return {contrast:(hi+.05)/(lo+.05),body:luminance(getComputedStyle(document.body).backgroundColor),
          logo:getComputedStyle(document.querySelector('.brand-icon rect')).fill,accent:style.backgroundColor,
          filter:getComputedStyle(document.querySelector('.package-art img')).filter};
      });
      expect(colors.contrast).toBeGreaterThan(4.5);
      expect(colors.logo).toBe(colors.accent);
      const icon=await page.evaluate(async()=>{
        const img=new Image();img.src=document.querySelector('link[rel=icon]').href;await img.decode();
        const canvas=document.createElement('canvas');canvas.width=canvas.height=64;
        const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0,64,64);
        return [...ctx.getImageData(32,4,1,1).data].slice(0,3);
      });
      expect(icon).toEqual(colors.logo.match(/[\d.]+/g).map(Number));
      expect(colors.filter).toBe('none');
      expect(await page.locator('.package-art img').getAttribute('src')).toBe(cover);
      if(mode==='dark')expect(colors.body).toBeLessThan(.05);else expect(colors.body).toBeGreaterThan(.85);
    }
  }
  await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
  await page.screenshot({path:'test-results/资源库-深色石墨灰.png',fullPage:false});
  await page.locator('[data-detail="artist.luna.2"]').first().click();
  await expect(page.locator('#drawer')).toBeVisible();
  expect(await page.locator('#drawer').evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(28, 32, 35)');
  await page.getByRole('button',{name:'关闭详情'}).click();
  await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
  await page.setViewportSize({width:650,height:900});
  await page.getByRole('button',{name:'海洋蓝'}).click();
  await expect(page.locator('[data-palette-choice=ocean]')).toHaveAttribute('aria-pressed','true');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:'test-results/主题设置-窄窗口.png',fullPage:false});
});

test('quick choices save in order and failed writes restore the saved appearance',async({page,request})=>{
  const {url,token}=connection();await page.goto(url+'/#'+token);
  await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
  await expect(page.locator('[data-palette-choice=forest]')).toBeVisible();
  await page.route('**/api/appearance',async route=>{
    const response=await route.fetch();await new Promise(r=>setTimeout(r,150));await route.fulfill({response});
  });
  await page.evaluate(()=>{
    for(const palette of ['ocean','rose','amber'])document.querySelector(`[data-palette-choice=${palette}]`).click();
    document.querySelector('[data-mode-choice=dark]').click();
  });
  await expect(page.locator('#appearance-status')).toHaveText('已自动保存');
  await expect.poll(()=>saved(request)).toEqual({palette:'amber',mode:'dark'});
  await expect(page.locator('html')).toHaveAttribute('data-palette','amber');
  await page.unroute('**/api/appearance');
  await page.route('**/api/appearance',route=>route.fulfill({status:400,json:{error:'测试保存失败'}}));
  await page.getByRole('button',{name:'森林绿'}).click();
  await expect(page.locator('#toast')).toContainText('测试保存失败');
  await expect(page.locator('html')).toHaveAttribute('data-palette','amber');
  expect(await saved(request)).toEqual({palette:'amber',mode:'dark'});
});
