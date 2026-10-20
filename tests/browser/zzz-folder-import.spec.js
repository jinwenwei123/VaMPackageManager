const {test,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

test('missing dependencies can activate; folder import preserves inactive mode, attachments and sources',async({page})=>{
  const {url,token,fixtureAddon,fixtureVault}=JSON.parse(fs.readFileSync('test-results/ui-server.json','utf8'));
  const headers={'X-Session-Token':token},errors=[];page.on('pageerror',e=>errors.push(e.message));
  const get=async route=>(await page.request.get(url+'/api/'+route,{headers})).json();
  const waitJob=async job=>{
    await expect.poll(async()=>(await get('job?id='+job.id)).status,{timeout:15000}).toBe('done');
    return (await get('job?id='+job.id)).result;
  };
  const post=async(route,data)=>{
    const response=await page.request.post(url+'/api/'+route,{headers,data});expect(response.ok()).toBe(true);
    const result=await response.json();return result.status==='running'?waitJob(result):result;
  };
  const paths=()=>fs.readdirSync(fixtureAddon,{recursive:true}).filter(p=>p.endsWith('.var')).sort();
  const originalPaths=paths(),source=path.resolve('test-results/文件夹导入测试');
  const sourceFiles=fs.readdirSync(source,{recursive:true}).filter(p=>fs.statSync(path.join(source,p)).isFile());
  const sourceHashes=Object.fromEntries(sourceFiles.map(p=>[p,hash(path.join(source,p))]));
  const profiles=await post('profile',{name:'缺依赖也启用测试',keys:['demo.needdependency.1']});
  const profile=profiles.find(p=>p.name==='缺依赖也启用测试');
  await page.goto(url+'/#'+token);
  await page.getByRole('button',{name:'▷ 启动方案',exact:true}).click();
  await page.locator('.profile').filter({hasText:profile.name}).getByRole('button',{name:'预览启用',exact:true}).click();
  const dialog=page.locator('#dialog');await expect(dialog).toContainText('仍可启用已有可用包');
  await expect(dialog.getByRole('button',{name:'确认执行'})).toBeEnabled();
  expect(paths()).toEqual(originalPaths); // Preview itself must not move packages.
  await page.screenshot({path:'test-results/缺失依赖-允许启用.png'});
  const applying=page.waitForResponse(r=>r.url()===url+'/api/apply');
  await dialog.getByRole('button',{name:'确认执行'}).click();
  const applied=await waitJob(await (await applying).json());
  await expect(page.locator('#job-panel')).toBeHidden({timeout:15000});
  expect((await get('package?key=demo.needdependency.1')).active).toBe(true);
  const activeBeforeImport=paths();expect(activeBeforeImport.length).toBe(1);
  await page.getByRole('button',{name:/⊞资源库|⊞ 资源库/}).click();
  await page.getByRole('button',{name:'＋ 添加资源'}).click();
  await expect(dialog).toContainText('.DisabledPackages/包管家导入/');
  await page.locator('#import-folder').setInputFiles(source);
  await expect(page.locator('#import-selection')).toContainText('1 个 VAR、4 个附件');
  await expect(page.locator('#import-selection')).toContainText('已忽略 1');
  await page.locator('#import-ai').uncheck();await page.locator('#import-find').uncheck();
  await page.screenshot({path:'test-results/文件夹导入-选择与目的地.png'});
  // Cancel during a streamed upload: finish the current file, discard it, import nothing.
  await page.route('**/api/upload?*',async route=>{
    const response=await route.fetch();await new Promise(r=>setTimeout(r,500));await route.fulfill({response});
  });
  await page.getByRole('button',{name:'导入并分析'}).click();
  await page.getByRole('button',{name:'停止上传'}).click();
  await expect(page.locator('#upload-progress')).toContainText('原文件和资源库保持不变',{timeout:15000});
  expect(paths()).toEqual(activeBeforeImport);
  await page.unroute('**/api/upload?*');
  await page.getByRole('button',{name:'导入并分析'}).click();
  await expect(page.getByRole('heading',{name:'资源导入完成'})).toBeVisible({timeout:15000});
  await expect(dialog).toContainText('VAR 成功 1 个，附件复制 4 个');
  await expect(dialog).toContainText('已按当前方案同步启停');
  expect(paths()).toEqual(activeBeforeImport);
  const imported=await get('package?key=folder.newlook.1');expect(imported.active).toBe(false);
  expect(imported.files[0].rel).toBe('人物/Folder.NewLook.1/子目录/Folder.NewLook.1.var');
  const dest=path.join(fixtureVault,'人物/Folder.NewLook.1');
  for(const [rel,digest] of Object.entries(sourceHashes)){
    expect(hash(path.join(source,rel))).toBe(digest);
    if(rel.endsWith('.exe'))expect(fs.existsSync(path.join(dest,rel))).toBe(false);
    else expect(hash(path.join(dest,rel))).toBe(digest);
  }
  await page.getByRole('button',{name:'完成',exact:true}).click();
  await page.getByLabel('搜索资源').fill('NewLook');
  await expect(page.locator('.package-row')).toHaveCount(1);
  await page.locator('[data-view=grid]').click();
  const checkbox=page.locator('.grid-view .package-row>input').first();
  await expect(checkbox).toBeVisible();
  expect(await checkbox.evaluate(el=>getComputedStyle(el).boxShadow)).toContain('0px 0px 0px 2px');
  await page.screenshot({path:'test-results/文件夹导入-资源网格.png'});
  await page.getByRole('button',{name:'收藏',exact:true}).click();
  await expect(page.getByRole('button',{name:'取消收藏',exact:true})).toBeVisible();
  await page.locator('#navigation [data-action=favorites]').click();
  await page.getByLabel('搜索资源').fill('NewLook');await expect(checkbox).toBeVisible();
  expect(await checkbox.evaluate(el=>getComputedStyle(el).boxShadow)).toContain('0px 0px 0px 2px');
  await page.screenshot({path:'test-results/文件夹导入-收藏网格.png'});
  const favorite=(await get('history')).find(h=>h.kind==='favorite'&&h.status==='done');
  await post('undo',{id:favorite.id});
  fs.rmSync(dest,{recursive:true,force:true});await post('scan',{});
  await post('undo',{id:applied.operation});await post('delete-profile',{id:profile.id});
  expect(paths()).toEqual(originalPaths);expect(errors).toEqual([]);
});
