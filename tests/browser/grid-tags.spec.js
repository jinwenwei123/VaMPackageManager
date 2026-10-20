const {test,expect}=require('@playwright/test');
const fs=require('fs');

test('grid cards align with passive tag counts and details show every full tag',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token},errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const items=(await (await page.request.get(url+'/api/packages',{headers})).json()).items.slice(0,6);
  const samples=[[],['短发'],['人物','长发','可爱风','幻想风'],
    ['辨识度很高的自定义长标签用于验证换行','这是另一个较长的标签名称','齐刘海及肩直发','室内暖色灯光','淡妆','人物展示'],
    Array.from({length:24},(_,i)=>'自定义标签'+i+'与较长的外观特征说明'),
    ['ABCDEFGHIJKLMNOPQRSTUVWXYZ1234','银白发','长发','柔和五官','半写实']];
  expect(items.length).toBe(6);
  const categorySamples=[['人物'],['人物','场景','服饰'],['环境道具','姿势动作','皮肤纹理','人物'],
    ['环境道具','姿势动作','皮肤纹理'],['人物','服饰','头发','场景'],['头发']];
  const withCategories=item=>({...item,categories:categorySamples[items.findIndex(original=>original.key===item.key)]||item.categories});
  await page.route('**/api/packages?*',async route=>{
    const response=await route.fetch(),data=await response.json();
    await route.fulfill({json:{...data,total:items.length,items:data.items.filter(item=>items.some(sample=>sample.key===item.key)).map(withCategories)}});
  });
  await page.route('**/api/package?*',async route=>{
    const response=await route.fetch();await route.fulfill({json:withCategories(await response.json())});
  });
  const label=async(data)=>{
    const response=await page.request.post(url+'/api/label',{headers,data});
    expect(response.ok()).toBe(true);
  };
  try{
    for(const [i,item] of items.entries())await label({key:item.key,summary:'用于比较卡片排版的简短资源简介。',tags:samples[i]});
    await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
    await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
    const cards=page.locator('.grid-view .package-row');
    await expect(cards).toHaveCount(items.length);
    const measure=()=>cards.evaluateAll(nodes=>nodes.map(card=>{
      const box=card.getBoundingClientRect(),info=card.querySelector('.package-info').getBoundingClientRect();
      const categories=card.querySelector('.category-badges').getBoundingClientRect();
      const name=card.querySelector('.package-name').getBoundingClientRect(),cover=card.querySelector('.package-art').getBoundingClientRect();
      return {height:box.height,summary:card.querySelector('.summary-wrap').getBoundingClientRect().top-info.top,
        categoriesTop:categories.top-box.top,belowTitle:categories.top>=name.bottom&&categories.top>=cover.bottom};
    }));
    const aligned=async()=>{
      const boxes=await measure();
      for(const property of ['height','summary','categoriesTop']){
        const values=boxes.map(box=>box[property]).filter(value=>value!==null);
        expect(Math.max(...values)-Math.min(...values),JSON.stringify({property,boxes})).toBeLessThan(1);
      }
      expect(boxes.every(box=>box.belowTitle)).toBe(true);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      return boxes;
    };
    for(const width of [1440,1100,760,420,1920]){
      await page.setViewportSize({width,height:1000});
      await aligned();
      await expect(page.locator('[data-tags-toggle]')).toHaveCount(0);
      for(const [i,item] of items.entries()){
        const card=page.locator(`.package-row[data-key="${item.key}"]`);
        await expect(card.locator('.content-tag')).toHaveCount(Math.min(samples[i].length,3));
        if(!samples[i].length)await expect(card.locator('.tag-placeholder')).toHaveText('暂无标签');
        else await expect(card.locator('.tag-placeholder')).toHaveCount(0);
        await expect(card.locator('.package-category')).toHaveCount(Math.min(categorySamples[i].length,3));
        if(categorySamples[i].length>3)await expect(card.locator('.category-more')).toHaveText('+'+(categorySamples[i].length-3));
        else await expect(card.locator('.category-more')).toHaveCount(0);
        if(samples[i].length>3)await expect(card.locator('.tags-more')).toHaveText('+'+(samples[i].length-3));
        else await expect(card.locator('.tags-more')).toHaveCount(0);
      }
      const card=page.locator(`.package-row[data-key="${items[4].key}"]`);
      await card.locator('.tags-more').click();
      await expect(page.locator('#drawer .content-tag:visible')).toHaveCount(24);
      await expect(page.locator('#drawer .package-category:visible')).toHaveCount(4);
      await expect(page.locator('#drawer .category-more')).toHaveCount(0);
      await expect(page.locator('#drawer .content-tags')).toHaveText(samples[4].join(''));
      await expect(page.locator('#drawer .tags-more,#drawer [data-tags-toggle]')).toHaveCount(0);
      expect(await page.locator('#drawer .content-tags').evaluate(row=>
        [...row.querySelectorAll('.content-tag')].every(tag=>tag.scrollWidth<=tag.clientWidth+1))).toBe(true);
      await page.getByRole('button',{name:'关闭详情',exact:true}).click();
      await aligned();
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:'test-results/大图标签-等高卡片.png',fullPage:true});
    expect(errors).toEqual([]);
  }finally{
    for(const item of items)await label({key:item.key,summary:item.summary,tags:item.tags||[]});
  }
});
