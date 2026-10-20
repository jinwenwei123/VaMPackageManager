const {test,expect}=require('@playwright/test'),fs=require('fs');

test('composer resizes from either end of its upper edge and retains a usable conversation',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token},errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.request.post(url+'/api/settings',{headers,data:{provider:'codex'}});
  await page.request.post(url+'/api/agent/reset',{headers,data:{}});
  await page.goto(url+'/#'+token);
  await page.getByRole('button',{name:'✧ 对话助手',exact:true}).click();
  const input=page.getByLabel('发送给助手的消息'),edge=page.getByRole('separator',{name:'调整输入框高度'});
  const height=()=>input.evaluate(node=>node.getBoundingClientRect().height);
  const geometry=()=>page.evaluate(()=>({
    thread:document.querySelector('#chat-thread').clientHeight,
    bottom:document.querySelector('#chat-form').getBoundingClientRect().bottom,
    overflow:document.documentElement.scrollWidth>innerWidth||document.documentElement.scrollHeight>innerHeight+1
  }));
  const drag=async(side,dy)=>{
    const box=await edge.boundingBox(),x=side==='left'?box.x+16:box.x+box.width-16,y=box.y+box.height/2;
    await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x,y+dy,{steps:8});await page.mouse.up();
  };
  await input.fill('保留输入中的草稿');
  expect(await input.evaluate(node=>getComputedStyle(node).resize)).toBe('none');
  const before=await geometry(),initial=await height();
  await drag('left',-120);
  expect(await height()).toBeCloseTo(initial+120,0);
  expect((await geometry()).bottom).toBeCloseTo(before.bottom,0);
  expect((await geometry()).thread).toBeLessThan(before.thread-100);
  await drag('right',64);
  const preferred=await height();expect(preferred).toBeCloseTo(initial+56,0);
  await expect(input).toHaveValue('保留输入中的草稿');
  await page.getByRole('button',{name:/⊞.*资源库/}).click();
  await page.getByRole('button',{name:'✧ 对话助手',exact:true}).click();
  expect(await height()).toBeCloseTo(preferred,0);await expect(input).toHaveValue('保留输入中的草稿');
  await page.reload();await page.getByRole('button',{name:'✧ 对话助手',exact:true}).click();
  expect(await height()).toBeCloseTo(preferred,0);
  await edge.focus();await page.keyboard.press('End');
  expect(await height()).toBe(Number(await edge.getAttribute('aria-valuemax')));
  await page.setViewportSize({width:390,height:700});
  await expect.poll(async()=>(await geometry()).thread).toBeGreaterThanOrEqual(150);
  await expect(page.getByRole('button',{name:'发送 ↑',exact:true})).toBeInViewport();
  expect((await geometry()).overflow).toBe(false);
  await edge.focus();await page.keyboard.press('Home');expect(await height()).toBe(48);
  await page.keyboard.press('ArrowUp');expect(await height()).toBe(64);
  await page.keyboard.press('ArrowDown');expect(await height()).toBe(48);
  await drag('left',100);expect(await height()).toBe(48);
  await input.fill('推荐 Luna');await input.press('Shift+Enter');await expect(input).toHaveValue('推荐 Luna\n');
  await input.press('Enter');await expect(page.locator('.chat-message.assistant')).toContainText('推荐本地 Luna');
  await expect(page.locator('#chat-send')).toBeEnabled();expect(await height()).toBe(48);
  await expect(page.locator('body')).not.toHaveClass(/chat-resizing/);
  await page.setViewportSize({width:1440,height:1000});
  await drag('right',-160);
  await page.screenshot({path:'test-results/对话输入框-边缘调高.png'});
  expect(errors).toEqual([]);
});
