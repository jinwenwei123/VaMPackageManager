const {test,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

test('scan, archive and favorite use the applied mode and preserve grouped files',async({page})=>{
  const {url,token,fixtureAddon,fixtureVault}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token},errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const get=async r=>(await page.request.get(url+'/api/'+r,{headers})).json();
  const done=async j=>{
    await expect.poll(async()=>(await get('job?id='+j.id)).status,{timeout:15000}).toBe('done');
    return (await get('job?id='+j.id)).result;
  };
  const post=async(route,data)=>{
    const response=await page.request.post(url+'/api/'+route,{headers,data});expect(response.ok()).toBe(true);
    const out=await response.json();return out.status==='running'?done(out):out;
  };
  // Start from an explicit inactive mode, independent of earlier profile tests.
  const staged=await post('plan',{kind:'stage_all',keys:[]});
  await post('apply',{id:staged.id});
  const dir=path.join(fixtureAddon,'手动下载','Multi.Bundle.1');fs.mkdirSync(dir,{recursive:true});
  const source=path.join(dir,'Multi.Bundle.1.var');fs.copyFileSync('test-results/Multi.Bundle.1.var',source);
  fs.writeFileSync(path.join(dir,'说明.txt'),'keep original note');const digest=hash(source);
  await page.goto(url+'/#'+token);await page.locator('#navigation [data-page="library"]').click();
  await page.locator('[data-action=scan]').click();
  await expect.poll(async()=>(await get('package?key=multi.bundle.1')).active).toBe(false);
  await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
  let pkg=await get('package?key=multi.bundle.1');expect(pkg.files[0].area).toBe('vault');
  expect(hash(path.join(fixtureVault,pkg.files[0].rel))).toBe(digest);
  expect(fs.existsSync(source)).toBe(false);

  const category=pkg.category==='人物'?'场景':'人物';
  await post('label',{key:'multi.bundle.1',category});
  await page.getByLabel('搜索资源').fill('Multi.Bundle');
  await expect(page.locator('.package-row')).toHaveCount(1);
  await page.locator('.package-row>input').check();
  await page.locator('[data-action=organize]').click();
  await expect(page.locator('#dialog')).toContainText('校正全库启停');
  await expect(page.locator('[data-action=apply-plan]')).toBeEnabled();
  const applying=page.waitForResponse(r=>r.url()===url+'/api/apply');
  await page.locator('[data-action=apply-plan]').click();await done(await(await applying).json());
  await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
  pkg=await get('package?key=multi.bundle.1');expect(pkg.files[0].rel).toBe(`${category}/Multi.Bundle.1/Multi.Bundle.1.var`);
  await page.getByRole('button',{name:'收藏',exact:true}).click();
  await expect(page.getByRole('button',{name:'取消收藏',exact:true})).toBeVisible();
  pkg=await get('package?key=multi.bundle.1');expect(pkg.active).toBe(false);
  expect(pkg.files[0].rel).toBe(`收藏/${category}/Multi.Bundle.1/Multi.Bundle.1.var`);
  expect(fs.readFileSync(path.join(fixtureVault,`收藏/${category}/Multi.Bundle.1/说明.txt`),'utf8')).toBe('keep original note');

  const profile=(await get('profiles')).find(p=>p.is_default);
  await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
  await page.getByRole('button',{name:'▷ 启动方案',exact:true}).click();
  await page.locator('.profile').filter({hasText:profile.name}).getByRole('button',{name:'预览启用',exact:true}).click();
  await expect(page.locator('[data-action=apply-plan]')).toBeEnabled();
  const activating=page.waitForResponse(r=>r.url()===url+'/api/apply');
  await page.locator('[data-action=apply-plan]').click();await done(await(await activating).json());
  await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
  pkg=await get('package?key=multi.bundle.1');expect(pkg.active).toBe(true);
  expect(hash(path.join(fixtureAddon,pkg.files[0].rel))).toBe(digest);
  await page.getByRole('button',{name:/⊞资源库|⊞ 资源库/}).click();
  await page.getByLabel('搜索资源').fill('Multi.Bundle');
  await expect(page.locator('.package-row')).toHaveCount(1);
  const scanning=page.waitForResponse(r=>r.url()===url+'/api/scan');
  await page.locator('[data-action=scan]').click();await done(await(await scanning).json());
  await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
  await page.getByRole('button',{name:'取消收藏',exact:true}).click();
  await expect(page.getByRole('button',{name:'收藏',exact:true})).toBeVisible();
  pkg=await get('package?key=multi.bundle.1');expect(pkg.active).toBe(false);
  expect(pkg.files[0].rel).toBe(`${category}/Multi.Bundle.1/Multi.Bundle.1.var`);
  expect(hash(path.join(fixtureVault,pkg.files[0].rel))).toBe(digest);
  expect(errors).toEqual([]);
  await page.screenshot({path:'test-results/扫描与归档-按当前方案同步.png'});
});
