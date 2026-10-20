const {test,expect}=require('@playwright/test');
const fs=require('fs'),os=require('os'),path=require('path'),{spawn,spawnSync}=require('child_process');

test('quarantine lists files, restores batches and requires confirmation before permanent cleanup',async({browser})=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vampm-quarantine-'));
  const home=path.join(root,'tool'),game=path.join(root,'game');
  fs.mkdirSync(home);fs.mkdirSync(game);fs.cpSync('web',path.join(home,'web'),{recursive:true});
  const setup=spawnSync('python3',['-c',`
import json,sys
from pathlib import Path
from vampm.server import Application
from vampm.util import Job
from test_manager import var_bytes
app=Application(Path(sys.argv[1]),Path(sys.argv[2]));ids=[]
for name in ['First.Look.1','Second.Look.1']:
    data=var_bytes(name)
    for folder in ['one','two']:
        file=app.c.roots['active']/folder/(name+'.var');file.parent.mkdir(exist_ok=True);file.write_bytes(data)
    app.c.scan(Job('scan'));app.c.duplicate_audit(Job('audit'))
    plan=app.ops.plan('dedup',[],Job('plan'));app.ops.apply(plan['id'],Job('apply'));ids.append(plan['id'])
app.c.label('first.look.1',summary='原有简介保留',tags=['原有标签'])
print(json.dumps(ids));app.agent.close();app.ai.close()
`,game,home],{encoding:'utf8',env:{...process.env,PYTHONPATH:[path.resolve('src'),path.resolve('tests')].join(path.delimiter)}});
  expect(setup.status,setup.stderr).toBe(0);
  const [first,second]=JSON.parse(setup.stdout);
  const proc=spawn('python3',[path.resolve('src/main.py'),'--home',home,'--game',game,'--no-browser'],{stdio:'ignore'});
  const context=await browser.newContext(),errors=[];
  try{
    const serverFile=path.join(home,'data/server.json');
    await expect.poll(()=>fs.existsSync(serverFile),{timeout:10000}).toBe(true);
    const {url,token}=JSON.parse(fs.readFileSync(serverFile,'utf8')),headers={'X-Session-Token':token};
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(url+'/#'+token);
    await page.locator('#navigation [data-page=duplicates]').click();
    await page.getByRole('button',{name:'查看隔离区',exact:true}).click();
    const panel=page.locator('#quarantine-panel');
    await expect(panel).toContainText('2 个文件');
    const batch=id=>panel.locator('tr').filter({has:page.locator(`[data-quarantine-batch="${id}"]`)});
    await batch(first).getByRole('button',{name:'查看文件'}).click();
    await expect(page.locator('#dialog')).toContainText('First.Look.1.var');
    await expect(page.locator('#dialog')).toContainText('原位置：AddonPackages');
    await page.locator('#dialog').getByRole('button',{name:'关闭',exact:true}).click();
    await panel.screenshot({path:'test-results/隔离区-批次.png'});
    await batch(second).getByRole('button',{name:'恢复整批'}).click();
    await expect(panel).toContainText('1 个文件',{timeout:15000});
    expect(fs.existsSync(path.join(game,'AddonPackages/two/Second.Look.1.var'))).toBe(true);
    await page.locator(`[data-quarantine-batch="${first}"]`).check();
    await panel.getByRole('button',{name:'清理选中批次'}).click();
    await expect(page.locator('#dialog h2')).toHaveText('永久清理隔离区');
    await expect(page.getByRole('button',{name:'确认永久删除',exact:true})).toBeDisabled();
    await page.locator('#dialog').getByRole('button',{name:'取消',exact:true}).click();
    await expect(panel).toContainText('1 个文件');
    await panel.getByRole('button',{name:'清理选中批次'}).click();
    await expect(page.locator('#dialog h2')).toHaveText('永久清理隔离区');
    await page.locator('#dialog').screenshot({path:'test-results/隔离区-永久清理确认.png'});
    // Files appearing after preview are outside the confirmed deletion snapshot.
    const storage=(await (await page.request.get(url+'/api/status',{headers})).json()).workspace.storage;
    const later=path.join(storage.paths.quarantine,'later.txt');fs.writeFileSync(later,'keep until next preview');
    await page.getByLabel('我确认永久删除这些隔离文件').check();
    await page.getByRole('button',{name:'确认永久删除',exact:true}).click();
    await expect(panel).toContainText('未关联操作记录的文件',{timeout:15000});
    await expect(panel.locator(`[data-quarantine-batch="${first}"]`)).toHaveCount(0);
    expect(fs.existsSync(later)).toBe(true);
    await page.setViewportSize({width:420,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/隔离区-窄屏.png',fullPage:true});
    await panel.getByRole('button',{name:'清空隔离区',exact:true}).click();
    await expect(page.locator('#dialog')).toContainText('later.txt');
    await page.getByLabel('我确认永久删除这些隔离文件').check();
    await page.getByRole('button',{name:'确认永久删除',exact:true}).click();
    await expect(panel).toContainText('隔离区为空',{timeout:15000});
    expect(fs.existsSync(later)).toBe(false);
    const original=await (await page.request.get(url+'/api/package?key=first.look.1',{headers})).json();
    expect(original.summary).toBe('原有简介保留');expect(original.tags).toEqual(['原有标签']);expect(original.files).toHaveLength(1);
    await page.reload();
    await page.locator('#navigation [data-page=duplicates]').click();
    await expect(panel).toContainText('隔离区为空');
    await expect(page.locator('#dialog')).not.toBeVisible();
    await page.locator('#navigation [data-page=history]').click();
    await expect(page.locator(`[data-undo="${first}"]`)).toBeDisabled();
    await expect(page.locator(`[data-undo="${first}"]`)).toHaveText('无法恢复');
    await expect(page.getByRole('heading',{name:/永久清理隔离区/})).toHaveCount(2);
    expect(errors).toEqual([]);
  }finally{
    await context.close().catch(()=>{});
    if(proc.exitCode===null)proc.kill();
    await expect.poll(()=>proc.exitCode!==null||proc.signalCode!==null).toBe(true);
    fs.rmSync(root,{recursive:true,force:true});
  }
});
