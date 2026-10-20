'use strict';
let workspaceReloading=false;
function reloadWorkspace(){
  if(workspaceReloading)return;workspaceReloading=true;stopped=true;clearTimeout(pollTimer);renderController?.abort();
  saveSelection();$('#main').inert=true;location.reload();
}
function adoptWorkspace(status){
  const next=status.workspace;if(!next)return;
  if(state.workspace&&state.workspace.epoch!==next.epoch){reloadWorkspace();throw new DOMException(tr('目录已切换'),'AbortError');}
  if(!state.workspace){
    selectionStorageKey=next.id==='original'?'vampm-selection':'vampm-selection-'+next.id;
    let selected=[];try{selected=JSON.parse(localStorage.getItem(selectionStorageKey)||'[]');}catch{}
    state.selected=new Set(Array.isArray(selected)?selected:[]);
  }
  state.workspace=next;state.directories=status.game_directories;
  paintGameStorage(status.busy||status.agent_busy);
  window.VaMPMWorkspace={id:next.id,epoch:next.epoch,reload:reloadWorkspace};
  const label=$('#workspace-name');if(label){label.textContent=next.id?next.name:tr(next.name);label.title=next.path;}
  $('#workspace-offline')?.classList.toggle('hidden',next.available!==false);
  const notice=$('#workspace-offline span');if(notice)notice.textContent=next.id?tr('当前游戏目录不可用，已有数据保留。请连接磁盘或重新绑定位置。'):tr('尚未选择游戏目录。请添加、恢复或选择目录，已移除目录的数据仍保留。');
}
async function switchWorkspace(id){
  if(!id||id===state.workspace?.id)return;
  await api('game-directories/switch',{id});reloadWorkspace();
}
function directorySettings(){
  const d=state.directories||{items:[]},current=d.items.find(p=>p.id===d.current),busy=state.busy||state.agentBusy;
  const folderFields=(value)=>ui`<label class="form-row"><span>启用文件夹（固定）</span><input value="AddonPackages" class="directory-fixed-folder" disabled aria-disabled="true"></label><label class="form-row"><span>停用文件夹名</span><input name="disabled_folder" required maxlength="80" value="${esc(value||'.DisabledPackages')}" autocomplete="off"></label>`;
  return ui`<section class="panel settings-panel" id="game-directories">
    <div class="panel-head"><div><h2>游戏目录</h2><p class="spaced">每个目录独立保存索引、简介、标签、收藏、方案、聊天和主页布局。</p></div><span class="badge">${d.items.length} 个目录</span></div>
    <div class="directory-list">${d.items.map(item=>ui`<div class="directory-row"><div><strong>${esc(item.name)}</strong>${item.id===d.current?ui('<span class="badge green">当前使用</span>'):''}<p>${esc(item.path)}</p><p>启用：AddonPackages · 停用：${esc(item.disabled_location==='legacy'?item.disabled_path:item.disabled_folder)}</p></div><div class="buttons"><button class="button small" data-workspace-switch="${esc(item.id)}" ${item.id===d.current||busy?'disabled':''}>${item.id===d.current?tr('使用中'):tr('切换')}</button><button class="button plain small" data-workspace-rename="${esc(item.id)}">改名</button><button class="button plain small" data-workspace-rebind="${esc(item.id)}" ${busy?'disabled':''}>更改位置</button><button class="button plain small" data-workspace-remove="${esc(item.id)}" ${busy?'disabled':''}>移除</button></div></div>`).join('')}</div>
    ${!d.items.length?ui('<p class="spaced">尚未保存游戏目录。可添加新目录，或恢复下方保留的数据。</p>'):''}
    ${d.archived?.length?ui`<details class="spaced"><summary>已移除目录的数据（${d.archived.length}）</summary><p>简介、标签、方案、聊天和设置均保留。重新添加原路径会自动沿用；目录搬家后可重新绑定位置。</p>${d.archived.map(item=>ui`<div class="directory-row directory-archived"><div><strong>${esc(item.name)}</strong><p>原位置：${esc(item.path)}</p><p>数据：${esc(item.data_path)}</p></div><button class="button small" data-workspace-rebind="${esc(item.id)}" ${busy?'disabled':''}>恢复／重新绑定</button></div>`).join('')}</details>`:''}
    ${current?ui`<form id="directory-folders-form" class="spaced"><h3>当前目录的资源文件夹</h3><div class="form-grid">${folderFields(current.disabled_folder)}</div><p>相对于游戏根目录。当前停用位置：${esc(current.disabled_path)}</p><p>保存时会将已有停用文件夹整体移到新名称，简介、标签和方案保留；目标已存在时不会合并或覆盖。请先关闭 VaM。${current.disabled_location==='legacy'?tr('当前沿用旧版 data/disabled，保存后迁移到游戏根目录。'):''}</p><button class="button" type="submit" ${busy||state.workspace?.available===false?'disabled':''}>保存资源文件夹</button></form>`:''}
    <div id="game-storage-panel" class="spaced">${gameStorageSettings(busy)}</div>
    <form id="directory-add-form" class="spaced"><h3>添加游戏目录</h3><div class="form-grid"><label class="form-row"><span>目录名称</span><input name="name" required maxlength="80" placeholder="选择游戏目录后自动填写"><small data-directory-name-error role="alert" hidden></small></label>${directoryPathField()}${folderFields('.DisabledPackages')}</div><p>两个文件夹均相对于新游戏根目录；停用文件夹可以自定义，已有内容在首次扫描时读取。重新添加已移除的原路径时，会恢复原数据及原停用文件夹设置。</p><button class="button" type="submit">＋ 保存目录</button></form>
    <p class="spaced">请选择包含 AddonPackages 的目录。原目录的数据保留在原位；添加及切换不扫描或移动资源。新目录首次沿用当前 AI 连接与配色，随后独立保存。</p><p class="spaced">切换目录或修改资源文件夹会刷新所有已打开页面，请先保存正在编辑的内容；正在生成、上传或执行操作时需先等待或暂停。路径暂时不可用时，已有数据仍保留。</p></section>`;
}
function renameDirectory(id){
  const item=state.directories?.items.find(p=>p.id===id);if(!item)return;
  openModal(ui`<h2>修改目录名称</h2><form id="directory-rename-form"><input type="hidden" name="directory_id" value="${esc(id)}"><label class="form-row"><span>名称</span><input name="name" required maxlength="80" value="${esc(item.name)}"><small data-directory-name-error role="alert" hidden></small></label><p>${esc(item.path)}</p><div class="modal-actions"><button type="button" class="button" data-action="close-modal">取消</button><button class="button primary" type="submit">保存名称</button></div></form>`);validateDirectoryForm($('#directory-rename-form'));
}

function directoryRecord(id){return [...(state.directories?.items||[]),...(state.directories?.archived||[])].find(p=>p.id===id);}
function rebindDirectory(id){
  const item=directoryRecord(id);if(!item)return;
  openModal(ui`<h2>重新绑定游戏目录</h2><form id="directory-rebind-form"><input type="hidden" name="directory_id" value="${esc(id)}"><p>${esc(item.name)} · 数据保留在 ${esc(item.data_path)}</p><label class="form-row"><span>目录名称</span><input name="name" required maxlength="80" value="${esc(item.name)}"><small data-directory-name-error role="alert" hidden></small></label>${directoryPathField(item.path)}<p>游戏搬家后填写新位置；恢复原位置可直接保存。请先移动完整游戏目录，包括停用文件夹「${esc(item.disabled_folder)}」和 .PackageManagerStorage 管理文件夹。绑定只更新位置，不搬动资源、不清空索引或简介。</p><div class="modal-actions"><button type="button" class="button" data-action="close-modal">取消</button><button class="button primary" type="submit">保存绑定</button></div></form>`);validateDirectoryForm($('#directory-rebind-form'));
}
function removeDirectory(id){
  const item=directoryRecord(id);if(!item)return;
  openModal(ui`<h2>移除游戏目录</h2><form id="directory-remove-form"><input type="hidden" name="directory_id" value="${esc(id)}"><p>${esc(item.name)}</p><p class="break">${esc(item.path)}</p><p>只从可切换列表中移除。游戏文件及简介、标签、方案、聊天等数据全部保留，可在“已移除目录的数据”中恢复或重新绑定。</p>${id===state.workspace?.id?ui('<p>这是当前目录，移除后将退出当前目录并刷新所有页面。</p>'):''}<div class="modal-actions"><button type="button" class="button" data-action="close-modal">取消</button><button class="button primary" type="submit">移除并保留数据</button></div></form>`);
}

function directoryPathField(value=''){
  return ui`<label class="form-row"><span>游戏根目录</span><span class="fieldline directory-path-picker"><input name="path" required value="${esc(value)}" placeholder="请选择 VaM 游戏根目录" autocomplete="off"><button type="button" class="button" data-directory-browse>选择文件夹</button></span></label>`;
}
function directoryPathName(value){return value.trim().replace(/^"|"$/g,'').replace(/[\\/]+$/g,'').split(/[\\/]/).pop()||'';}
function validateDirectoryForm(form){
  const input=form?.elements.namedItem('name');if(!input)return true;
  const name=input.value.trim(),id=form.elements.namedItem('directory_id')?.value;
  const duplicate=(state.directories?.items||[]).some(item=>item.id!==id&&item.name.trim().toLocaleLowerCase()===name.toLocaleLowerCase());
  const message=duplicate?tr('目录名称已存在，请修改名称后再保存。'):'';
  input.setCustomValidity(message);
  const notice=$('[data-directory-name-error]',form);if(notice){notice.textContent=message;notice.hidden=!message;}
  $('button[type=submit]',form).disabled=duplicate||!name;
  return !!name&&!duplicate;
}
document.addEventListener('input',event=>{
  const form=event.target.closest('#directory-add-form,#directory-rename-form,#directory-rebind-form,#onboarding-directory-form');if(!form)return;
  if(['directory-add-form','onboarding-directory-form'].includes(form.id)&&event.target.name==='path'){
    const name=form.elements.namedItem('name'),base=directoryPathName(event.target.value);
    if(!name.value.trim()||name.value===name.dataset.autoName)name.value=base;
    name.dataset.autoName=base;
  }
  validateDirectoryForm(form);
});
async function browseDirectory(button){
  const form=button.closest('form'),path=form.elements.namedItem('path');
  button.disabled=true;button.textContent=tr('选择中…');
  try{
    const result=await api('game-directories/browse',{path:path.value.trim()});
    if(!result.cancelled&&form.isConnected){path.value=result.path;path.dispatchEvent(new Event('input',{bubbles:true}));}
  }finally{if(button.isConnected){button.disabled=false;button.textContent=tr('选择文件夹');}}
}

function gameStorageSettings(busy=state.busy||state.agentBusy){
  const storage=state.workspace?.storage;if(!storage)return '';
  const legacy=new Set(storage.legacy),labels={imports:tr('导入暂存'),downloads:tr('下载暂存'),quarantine:tr('隔离区')};
  return ui`<h3>游戏管理文件</h3><p>索引、简介、标签、方案和恢复记录保留在管家的 data 中；大文件保存在对应游戏目录，减少跨盘搬运。</p><p class="break">数据目录：${esc(storage.metadata)}</p>${Object.entries(labels).map(([kind,label])=>`<p class="break">${label}：${esc(storage.paths[kind])}${legacy.has(kind)?ui(' <span class="badge">旧位置</span>'):''}</p>`).join('')}
    ${legacy.size||storage.pending?ui`<p>旧文件在迁移前仍沿用原位置。迁移会逐项校验，不合并或覆盖同名内容；中断后可继续。迁移目标：<span class="break">${esc(storage.folder)}</span></p><button class="button" data-storage-migrate ${busy||state.workspace.available===false?'disabled':''}>${storage.pending?tr('继续迁移'):tr('迁移旧管理文件')}</button>`:ui('<p>暂存和隔离区已使用游戏目录内的 .PackageManagerStorage。</p>')}
    ${storage.pending?ui('<p role="alert">上次迁移尚未完成，请继续迁移后再进行资源操作。</p>'):''}<p>搬移游戏时请一并移动 .PackageManagerStorage；移除游戏登记会保留这里的文件。</p>`;
}
function paintGameStorage(busy){
  const panel=$('#game-storage-panel');if(!panel)return;
  const html=gameStorageSettings(busy);if(panel.innerHTML!==html)panel.innerHTML=html;
}
