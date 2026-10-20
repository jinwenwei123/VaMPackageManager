const {test,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

test('copy selection, remembering, union activation, original names and recovery',async({page})=>{
  const fixture=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const {url,token,fixtureAddon,fixtureVault}=fixture,headers={'X-Session-Token':token};
  expect(fixtureAddon).toContain('game'); // Only the throwaway UI library is mutated.
  const get=async route=>(await page.request.get(url+'/api/'+route,{headers})).json();
  const post=async(route,data)=>{
    const response=await page.request.post(url+'/api/'+route,{headers,data});
    expect(response.ok()).toBe(true);let result=await response.json();
    if(result.status==='running'){
      const id=result.id;
      await expect.poll(async()=>(await get('job?id='+id)).status,{timeout:15000}).toBe('done');
      result=(await get('job?id='+id)).result;
    }
    return result;
  };
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const activeDir=path.join(fixtureAddon,'副本选择测试'),vaultDir=path.join(fixtureVault,'副本选择测试');
  fs.mkdirSync(activeDir,{recursive:true});fs.mkdirSync(vaultDir,{recursive:true});
  let original=path.join(activeDir,'Copies.Scene.1.var'),alternate=path.join(vaultDir,'Copies.Scene.1.var');
  fs.copyFileSync('test-results/CopyVariantA.var',original);
  fs.copyFileSync('test-results/CopyVariantB.var',alternate);
  for(const p of ['Copies.Hair.1','Copies.Outfit.1'])fs.copyFileSync('test-results/'+p+'.var',path.join(vaultDir,p+'.var'));
  const originalHash=hash(original),alternateHash=hash(alternate);
  await post('scan',{});
  // Scanning synchronizes the applied launch mode; preview/undo use that baseline.
  const scanned=(await get('package?key=copies.scene.1')).files.map(f=>path.join(f.area==='active'?fixtureAddon:fixtureVault,f.rel));
  original=scanned.find(file=>hash(file)===originalHash);alternate=scanned.find(file=>hash(file)===alternateHash);
  const profiles=await post('profile',{name:'副本选择测试',keys:['copies.scene.1']});
  const saved=profiles.find(p=>p.name==='副本选择测试');
  await page.goto(url+'/#'+token);
  await page.getByRole('button',{name:'▷ 启动方案',exact:true}).click();
  const card=page.locator('.profile').filter({hasText:'副本选择测试'});
  const dialog=page.locator('#dialog');
  await card.getByRole('button',{name:'预览启用',exact:true}).click();
  await expect(dialog).toContainText('待选择');
  await expect(dialog.getByRole('button',{name:'确认执行'})).toBeDisabled();
  await expect(dialog.locator('[data-copy-key]')).toHaveValue('');
  await dialog.locator('[data-copy-key]').selectOption(alternateHash);
  await expect(dialog.locator('#copy-preview-status')).toContainText('选择已变更');
  await dialog.getByRole('button',{name:'更新预览'}).click();
  await expect(dialog).toContainText('合计启用 2');
  await expect(dialog.locator('#copy-preview-status')).toHaveText('选择已记住。');
  await expect(dialog.getByRole('button',{name:'确认执行'})).toBeEnabled();
  await expect(dialog.locator('[data-copy-key]')).toHaveValue(alternateHash);
  expect(fs.existsSync(original)&&fs.existsSync(alternate)).toBe(true);
  await page.screenshot({path:'test-results/同名副本-指定预览.png'});
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await page.reload();await page.getByRole('button',{name:'▷ 启动方案',exact:true}).click();
  await card.getByRole('button',{name:'预览启用',exact:true}).click();
  await expect(dialog.locator('[data-copy-key]')).toHaveValue(alternateHash);
  // All mode must recompute the closure, retaining dependencies from both variants.
  await dialog.locator('[name=copy-mode][value=all]').check();
  await expect(dialog.locator('[data-copy-key]')).toBeDisabled();
  await expect(dialog.getByRole('button',{name:'确认执行'})).toBeDisabled();
  await dialog.getByRole('button',{name:'更新预览'}).click();
  await expect(dialog).toContainText('合计启用 3');
  await expect(dialog).toContainText('4 份文件');
  await expect(dialog).toContainText('副本-');
  await expect(dialog.locator('[name=copy-mode][value=all]')).toBeChecked();
  await page.setViewportSize({width:390,height:800});
  await dialog.locator('.copy-details summary').click();
  expect(await dialog.evaluate(el=>el.scrollWidth>el.clientWidth+1)).toBe(false);
  await page.screenshot({path:'test-results/同名副本-窄屏.png'});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'test-results/同名副本-全部保留.png'});
  const applying=page.waitForResponse(r=>r.url()===url+'/api/apply'&&r.request().method()==='POST');
  await dialog.getByRole('button',{name:'确认执行'}).click();
  const applyJob=await (await applying).json();
  await expect.poll(async()=>(await get('job?id='+applyJob.id)).status,{timeout:15000}).toBe('done');
  await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
  const detail=await get('package?key=copies.scene.1');
  expect(detail.files.every(f=>f.area==='active')).toBe(true);
  expect(detail.files.every(f=>path.basename(f.rel)==='Copies.Scene.1.var')).toBe(true);
  expect(detail.files.map(f=>hash(path.join(fixtureAddon,f.rel))).sort()).toEqual([originalHash,alternateHash].sort());
  expect((await get('package?key=copies.outfit.1')).active).toBe(true);
  expect((await get('package?key=copies.hair.1')).active).toBe(true);
  const operation=(await get('history')).find(o=>o.profile_id===saved.id&&o.status==='done');
  await post('undo',{id:operation.id});
  expect(hash(original)).toBe(originalHash);expect(hash(alternate)).toBe(alternateHash);
  await post('delete-profile',{id:saved.id});
  fs.rmSync(activeDir,{recursive:true,force:true});fs.rmSync(vaultDir,{recursive:true,force:true});
  await post('scan',{});
  expect(errors).toEqual([]);
});
