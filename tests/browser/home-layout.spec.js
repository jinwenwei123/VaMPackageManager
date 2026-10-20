const {test,expect}=require('@playwright/test');
const fs=require('fs');

test('home cards can be added, removed, reordered, persisted and reset',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token};
  const original=await (await page.request.get(url+'/api/home-layout',{headers})).json();
  const ids=()=>page.locator('.home-card').evaluateAll(nodes=>nodes.map(n=>n.dataset.homeCard));
  const edit=()=>page.getByRole('button',{name:'自定义主页',exact:true}).click();
  const save=()=>page.getByRole('button',{name:'保存布局',exact:true}).click();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto(url+'/#'+token);
    await edit();await expect(page.locator('[data-home-layout-row]')).toHaveCount(16);
    // Remove every card, then add a small custom selection.
    for(const checkbox of await page.locator('[data-home-enabled]').all())await checkbox.uncheck();
    await save();await expect(page.locator('.home-card')).toHaveCount(0);
    await page.getByRole('button',{name:'添加卡片',exact:true}).click();
    for(const id of ['directories','recent','categories','assistant','index'])await page.locator(`[data-home-enabled="${id}"]`).check();
    const move=page.getByRole('button',{name:'上移最近更新资源',exact:true});
    while(await move.isEnabled())await move.click();
    await page.locator('#home-layout-rows').evaluate(el=>{el.scrollTop=0;});
    await page.locator('[data-home-drag="directories"]').dragTo(page.locator('[data-home-layout-row="recent"]'));
    await expect(page.locator('[data-home-layout-row]').first()).toHaveAttribute('data-home-layout-row','directories');
    await move.click();
    await save();
    await expect.poll(ids).toEqual(['recent','directories','categories','assistant','index']);
    await page.reload();
    await expect.poll(ids).toEqual(['recent','directories','categories','assistant','index']);
    await expect(page.locator('[data-home-card=recent] [data-detail]')).toHaveCount(3);
    await page.locator('[data-home-card=recent] [data-detail]').first().click();
    await expect(page.locator('#drawer')).toBeVisible();
    await page.locator('#drawer [data-action=close-drawer]').click();
    await page.screenshot({path:'test-results/主页-自定义卡片.png',fullPage:true});
    await edit();
    await page.screenshot({path:'test-results/主页-卡片编辑.png',fullPage:true});
    await page.setViewportSize({width:420,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/主页-卡片编辑窄屏.png',fullPage:true});
    await page.getByRole('button',{name:'恢复默认',exact:true}).click();
    await save();await expect(page.locator('.home-card')).toHaveCount(10);
    expect(errors).toEqual([]);
  }finally{await page.request.post(url+'/api/home-layout',{headers,data:{cards:original.cards}});}
});
