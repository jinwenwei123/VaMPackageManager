const {test,expect}=require('@playwright/test');
const fs=require('fs');
const connection=()=>JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));

test('effort selectors are removed while model discovery and independent model choices still work',async({page,request})=>{
  const {url,token}=connection(),headers={'X-Session-Token':token};
  const before=await(await request.get(url+'/api/settings',{headers})).json();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const save=async()=>{
    const previous=await page.locator('#settings-form').elementHandle();
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await expect.poll(()=>previous.evaluate(el=>el.isConnected)).toBe(false);
    await expect(page.locator('#settings-form')).toBeVisible();
  };
  await page.route('**/api/settings',async route=>{
    if(route.request().method()!=='GET')return route.continue();
    const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),has_key:true}});
  });
  try{
    await request.post(url+'/api/settings',{headers,data:{codex_effort:'max',api_summary_effort:'max'}});
    await page.goto(url+'/#'+token);await page.getByRole('button',{name:'⚙ AI 与设置'}).click();
    for(const provider of ['codex','deepseek','compatible']){
      await page.locator('#ai-provider').selectOption(provider);
      await expect(page.locator('[data-model-effort], [name$="_effort"]')).toHaveCount(0);
      const prefix=provider==='codex'?'codex':'api';
      const chat=provider==='codex'?'gpt-6.1-sol':'deepseek-flash';
      const summary=provider==='codex'?'gpt-6-luna':'deepseek-v4-pro';
      const chatSelect=page.locator(`[name=${prefix}_model]`),summarySelect=page.locator(`[name=${prefix}_summary_model]`);
      if(provider!=='compatible'){
        await expect(chatSelect.locator(`option[value="${chat}"]`)).toHaveCount(1);
        await chatSelect.selectOption(chat);await summarySelect.selectOption(summary);
      }else{await chatSelect.fill(chat);await summarySelect.fill(summary);}
      await save();
      await expect(chatSelect).toHaveValue(chat);await expect(summarySelect).toHaveValue(summary);
      await page.locator(`[data-refresh-models=${prefix}]`).click();
      await expect(chatSelect).toHaveValue(chat);await expect(summarySelect).toHaveValue(summary);
      const settings=await(await request.get(url+'/api/settings',{headers})).json();
      expect(Object.keys(settings).filter(k=>k.endsWith('_effort'))).toEqual([]);
      await page.locator('#settings-form').screenshot({path:`test-results/统一中等-${provider}.png`});
    }
    await page.setViewportSize({width:390,height:700});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  }finally{await request.post(url+'/api/settings',{headers,data:before});}
});
