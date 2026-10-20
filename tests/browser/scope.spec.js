const {test,expect}=require('@playwright/test');
const fs=require('fs');

test('favorite facets, summary, status and bulk selection share the same scope',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token},errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const get=async route=>(await page.request.get(url+'/api/'+route,{headers})).json();
  const post=async(route,data)=>{
    const r=await page.request.post(url+'/api/'+route,{headers,data});expect(r.ok()).toBe(true);
    const out=await r.json();
    if(out.id)await expect.poll(async()=>(await get('job?id='+out.id)).status,{timeout:15000}).toBe('done');
    return out;
  };
  const ids=[];
  for(let i=0;i<44;i++){
    const name=`Scope.Item${String(i).padStart(2,'0')}.1.var`;
    const r=await page.request.post(url+'/api/upload?name='+name,{headers:{...headers,'Content-Type':'application/octet-stream'},data:fs.readFileSync('test-results/'+name)});
    expect(r.ok()).toBe(true);ids.push((await r.json()).id);
  }
  await post('import',{ids,ai:false,find:false});
  const keys=Array.from({length:43},(_,i)=>`scope.item${String(i).padStart(2,'0')}.1`);
  await post('favorite',{keys,favorite:true});
  // Earlier browser flows may leave a different launch mode applied.
  const profile=(await get('profiles')).find(p=>p.is_default);
  const planned=await post('plan',{kind:'activate',profile_id:profile.id});
  await post('apply',{id:(await get('job?id='+planned.id)).result.id});
  await page.goto(url+'/#'+token);
  await page.locator('#navigation [data-action=favorites]').click();
  await expect(page.getByRole('heading',{name:'收藏夹',exact:true})).toBeVisible();
  await expect(page.locator('#breadcrumb')).toHaveText('收藏夹');
  const input=page.getByLabel('搜索资源');
  await input.evaluate(el=>el.dataset.preserved='yes');
  await input.fill('Scope.Item');
  const chip=c=>page.locator(`[data-category="${c}"] small`);
  await expect(chip('')).toHaveText('43');
  await expect(chip('人物')).toHaveText('42');
  await expect(chip('场景')).toHaveText('1');
  await expect(chip('头发')).toHaveText('0');
  await expect(page.locator('#library-stats .stat-value').first()).toHaveText('43');
  await expect(input).toBeFocused();await expect(input).toHaveAttribute('data-preserved','yes');
  await page.locator('[data-category="人物"]').click();
  await expect(page.locator('#library-stats .stat-value').first()).toHaveText('42');
  await expect(chip('场景')).toHaveText('1');
  await page.locator('[data-category=""]').click();
  await page.locator('#state-filter').selectOption('active');
  await expect(page.getByRole('heading',{name:'收藏夹',exact:true})).toBeVisible();
  await expect(chip('')).toHaveText('43');
  await page.locator('#state-filter').selectOption('disabled');
  await expect(chip('')).toHaveText('0');
  await expect(page.getByRole('heading',{name:'没有匹配的资源'})).toBeVisible();
  await page.locator('#state-filter').selectOption('');
  await page.getByRole('button',{name:'选择全部 43 个结果'}).click();
  await expect(page.locator('#selection-bar b')).toHaveText('43');
  const selected=await page.evaluate(()=>[...state.selected].sort());
  expect(selected).toEqual(keys);
  await page.getByRole('button',{name:'清空',exact:true}).click();
  await page.getByRole('button',{name:'下一页',exact:true}).click();
  await expect(page.locator('.package-row')).toHaveCount(1);
  await page.locator('.package-row').getByRole('button',{name:'取消收藏',exact:true}).click();
  await expect(page.locator('.pagination')).toContainText('1 / 1 页',{timeout:15000});
  await expect(page.locator('.package-row')).toHaveCount(42);
  await expect(chip('')).toHaveText('42');
  await expect(chip('场景')).toHaveText('0');
  // Polling after a change updates counts without replacing the search input.
  await input.focus();await input.evaluate(el=>el.dataset.preserved='after-filter');
  await post('label',{key:keys[0],category:'服饰'});
  await expect(chip('服饰')).toHaveText('1',{timeout:12000});
  await expect(chip('人物')).toHaveText('42');
  await expect(input).toBeFocused();await expect(input).toHaveAttribute('data-preserved','after-filter');
  await page.screenshot({path:'test-results/收藏范围筛选计数.png',fullPage:true});
  await page.locator('#navigation [data-page=library]').click();
  await expect(page.getByRole('heading',{name:'资源库',exact:true})).toBeVisible();
  await expect(input).toHaveValue('');
  await expect(chip('')).toHaveText(String((await get('status')).stats.packages));
  await page.locator('#state-filter').selectOption('favorite');
  await expect(page.getByRole('heading',{name:'收藏夹',exact:true})).toBeVisible();
  await page.locator('#state-filter').selectOption('active');
  await expect(chip('')).toHaveText(String((await get('status')).stats.favorites));
  expect(errors).toEqual([]);
});

test('late select-all response cannot add packages from a previous filter',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token};
  const luna=await (await page.request.get(url+'/api/package?key=artist.luna.2',{headers})).json();
  await page.request.post(url+'/api/label',{headers,data:{key:luna.key,favorite:true}});
  await page.goto(url+'/#'+token);
  await page.locator('#navigation [data-action=favorites]').click();
  await expect(page.getByRole('heading',{name:'收藏夹',exact:true})).toBeVisible();
  let release,waiting=false;
  const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/package-keys?*',async route=>{
    const response=await route.fetch();waiting=true;await gate;await route.fulfill({response});
  });
  await page.locator('[data-action=select-results]').click();
  await expect.poll(()=>waiting).toBe(true);
  await page.getByLabel('搜索资源').fill('Luna');
  await expect(page.locator('.package-row')).toHaveCount(1);
  release();
  await expect(page.locator('#toast')).toContainText('筛选或资源已变化');
  await expect(page.locator('#selection-bar')).toBeHidden();
  expect(await page.evaluate(()=>state.selected.size)).toBe(0);
  await page.request.post(url+'/api/label',{headers,data:{key:luna.key,favorite:luna.favorite}});
});
