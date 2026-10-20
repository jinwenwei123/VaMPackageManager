const {test,expect}=require('@playwright/test');
const fs=require('fs');

test('10001 files register in bounded batches; a failed start cleans the entire session',async({page})=>{
  const {url,token}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const batches=[],discarded=[],errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{
    if(r.url().endsWith('/api/import-session/append'))batches.push(r.postDataJSON().ids.length);
  });
  page.on('response',async r=>{
    if(r.url().endsWith('/api/import-session/discard'))discarded.push(await r.json());
  });
  let submitted;
  // Real session registration/cleanup, synthetic uploads, no actual resource mutation.
  await page.route('**/api/import',async route=>{
    submitted=route.request().postDataJSON();
    await route.fulfill({status:400,json:{error:'fixture: start rejected'}});
  });
  await page.goto(url+'/#'+token);
  await page.getByRole('button',{name:/⊞资源库|⊞ 资源库/}).click();
  await page.getByRole('button',{name:'＋ 添加资源'}).click();
  await expect(page.locator('#import-files')).toBeVisible();
  await page.evaluate(()=>{
    const transfer=new DataTransfer();
    for(let i=0;i<10001;i++)transfer.items.add(new File(['x'],`Fixture.Item${i}.1.var`));
    const input=document.querySelector('#import-files');
    input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
    let id=0;
    window.VaMPMImports.upload=async(file,token,progress)=>{
      progress(file.size);
      return {id:(++id).toString(16).padStart(32,'0')};
    };
  });
  await expect(page.locator('#import-selection')).toContainText('10001 个 VAR');
  await expect(page.locator('#import-selection')).toContainText('自动分批登记');
  await page.getByRole('button',{name:'导入并分析'}).click();
  await expect.poll(()=>discarded.length).toBe(1);
  expect(batches).toEqual([...Array(10).fill(1000),1]);
  expect(submitted.session).toMatch(/^[0-9a-f]{32}$/);
  expect(submitted.ids).toBeUndefined();
  expect(discarded[0].discarded).toBe(10001);
  await expect(page.getByRole('button',{name:'导入并分析'})).toBeEnabled();
  expect(errors).toEqual([]);
});
