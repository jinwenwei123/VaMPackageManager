const {test,expect}=require('@playwright/test'),fs=require('fs');
const connection=()=>JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
const description='人物外观预设，搭配自然的发型、服饰和配饰，可用于室内展示或已有场景。'.repeat(6);
const measures=page=>page.evaluate(()=>({
  thread:document.querySelector('#chat-thread').clientHeight,
  shell:document.querySelector('.chat-shell').clientHeight,
  bottom:innerHeight-document.querySelector('.chat-shell').getBoundingClientRect().bottom,
  horizontal:document.documentElement.scrollWidth>innerWidth,
  vertical:document.documentElement.scrollHeight>innerHeight+1
}));

test('compact grid folds long summaries, expands independently and still opens full details',async({page})=>{
  const {url,token}=connection(),headers={'X-Session-Token':token};
  const original=await (await page.request.get(url+'/api/package?key=artist.luna.2',{headers})).json();
  try{
    await page.request.post(url+'/api/label',{headers,data:{key:'artist.luna.2',category:'人物',summary:description}});
    await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
    await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();
    const card=page.locator('.package-row[data-key="artist.luna.2"]'),text=card.locator('.package-summary'),toggle=card.locator('[data-summary-toggle]');
    await expect(toggle).toBeVisible();
    await expect.poll(()=>text.evaluate(el=>getComputedStyle(el).webkitLineClamp)).toBe('2');
    expect(await text.evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true);
    const collapsed=(await card.boundingBox()).height;
    // Grid cards reserve one preview row even when no content tags exist.
    expect(collapsed).toBeLessThan(490);expect(collapsed).toBeGreaterThan(400);
    fs.writeFileSync('test-results/compact-layout.json',JSON.stringify({grid:{height:collapsed,width:(await card.boundingBox()).width,cover:(await card.locator('.package-art').boundingBox()).height}}));
    await toggle.click();await expect(toggle).toHaveAttribute('aria-expanded','true');
    await expect(page.locator('#drawer-backdrop')).toBeHidden();
    expect((await card.boundingBox()).height).toBeGreaterThan(collapsed+60);
    expect(await text.evaluate(el=>el.scrollHeight<=el.clientHeight+1)).toBe(true);
    await page.getByRole('button',{name:'☷ 列表',exact:true}).click();await expect(toggle).toBeHidden();
    await page.getByRole('button',{name:'⊞ 大图',exact:true}).click();await expect(toggle).toBeVisible();await expect(toggle).toHaveAttribute('aria-expanded','true');
    await toggle.focus();await page.keyboard.press('Space');await expect(toggle).toHaveAttribute('aria-expanded','false');
    expect((await card.boundingBox()).height).toBeCloseTo(collapsed,0);
    await text.click();await expect(page.locator('#drawer .detail-summary')).toHaveText(description);
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-category="依赖"]')).toHaveCount(0);
    await page.screenshot({path:'test-results/1.8-紧凑大图.png'});
  }finally{await page.request.post(url+'/api/label',{headers,data:{key:original.key,category:original.category,summary:original.summary}});}
});

test('conversation fills the viewport and compact recommendations have independent disclosures',async({page})=>{
  const {url,token}=connection(),headers={'X-Session-Token':token};
  const original=await (await page.request.get(url+'/api/package?key=artist.luna.2',{headers})).json();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.request.post(url+'/api/label',{headers,data:{key:original.key,category:'人物',summary:description}});
    await page.request.post(url+'/api/settings',{headers,data:{provider:'codex'}});
    await page.goto(url+'/#'+token);await page.getByRole('button',{name:'✧ 对话助手',exact:true}).click();
    await page.getByRole('button',{name:'新对话',exact:true}).click();
    await expect(page.locator('.chat-suggestions')).toBeVisible();
    const before=await measures(page);expect(before.thread).toBeGreaterThan(690);expect(before.vertical).toBe(false);
    await page.getByLabel('发送给助手的消息').fill('推荐一个人物包');await page.getByRole('button',{name:'发送 ↑'}).click();
    const card=page.locator('.recommendation-card'),toggle=card.locator('[data-summary-toggle]');
    await expect(card).toHaveCount(1);await expect(toggle).toBeVisible();
    await expect(page.locator('.chat-suggestions')).toBeVisible();
    const desktop=await measures(page);expect(desktop.thread).toBeGreaterThanOrEqual(before.thread-1);expect(desktop.bottom).toBeLessThanOrEqual(20);
    expect(desktop.horizontal).toBe(false);expect(desktop.vertical).toBe(false);
    // Category badges and the empty-tag placeholder each occupy a row below the title.
    const collapsed=(await card.boundingBox()).height;expect(collapsed).toBeLessThan(400);
    if(!original.tags?.length)await expect(card.locator('.tag-placeholder')).toHaveText('暂无标签');
    const report=fs.existsSync('test-results/compact-layout.json')?JSON.parse(fs.readFileSync('test-results/compact-layout.json')):{};
    Object.assign(report,{chat_empty:before,chat:desktop,recommendation:{height:collapsed,width:(await card.boundingBox()).width}});
    fs.writeFileSync('test-results/compact-layout.json',JSON.stringify(report,null,2));
    await toggle.click();await expect(toggle).toHaveAttribute('aria-expanded','true');
    await expect(page.locator('#drawer-backdrop')).toBeHidden();
    expect((await card.boundingBox()).height).toBeGreaterThan(collapsed+60);
    await toggle.click();
    await card.locator('.package-summary').click();await expect(page.locator('#drawer .detail-summary')).toHaveText(description);
    await page.keyboard.press('Escape');
    await page.screenshot({path:'test-results/1.8-扩展聊天区域.png'});
    await page.setViewportSize({width:2560,height:1440});expect((await measures(page)).thread).toBeGreaterThan(desktop.thread+300);
    await page.setViewportSize({width:390,height:700});
    const narrow=await measures(page);expect(narrow.horizontal).toBe(false);expect(narrow.vertical).toBe(false);expect(narrow.thread).toBeGreaterThan(300);
    await expect(page.getByRole('button',{name:'发送 ↑'})).toBeInViewport();
    await page.screenshot({path:'test-results/1.8-窄窗口聊天.png'});
    await page.locator('#navigation [data-page=library]').click();
    await expect(page.locator('body')).not.toHaveClass(/assistant-view/);await expect(page.locator('footer')).toBeVisible();
    expect(errors).toEqual([]);
  }finally{
    await page.request.post(url+'/api/label',{headers,data:{key:original.key,category:original.category,summary:original.summary}});
    await page.request.post(url+'/api/agent/reset',{headers,data:{}});
  }
});
