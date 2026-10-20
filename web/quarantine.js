'use strict';
const quarantine={
  selected:new Set(),page:1,preview:null,pendingPreview:null,
  label(batch){return batch.id==='untracked'?tr('未关联操作记录的文件'):`${batch.created} · ${batch.id.slice(0,8)}`;},
  async panel(){
    const data=await api('quarantine');this.data=data;
    this.selected=new Set([...this.selected].filter(id=>data.batches.some(b=>b.id===id)));
    const count=Math.max(1,Math.ceil(data.batches.length/40));this.page=Math.min(this.page,count);
    const rows=data.batches.slice((this.page-1)*40,this.page*40);
    return ui`<section class="panel" id="quarantine-panel"><div class="panel-head"><div><h2>隔离区</h2><p class="spaced">${fmt(data.count)} 个文件 · ${bytes(data.bytes)} · ${fmt(data.batches.length)} 个批次</p></div><button class="button small" data-quarantine-action="refresh">刷新隔离区</button></div><p class="spaced break">位置：${esc(data.path)}</p><div class="notice spaced">重复副本暂存于此，仍占用磁盘。可查看文件或恢复整批；永久清理会删除隔离文件，不经过回收站，相关批次将无法整批恢复。</div>${data.skipped?ui`<p class="notice warning spaced">${fmt(data.skipped)} 个链接或特殊文件已跳过，不会清理其目标。</p>`:''}${rows.length?ui`<div class="buttons spaced"><button class="button danger small" data-quarantine-action="selected" ${this.selected.size?'':'disabled'}>清理选中批次</button><button class="button danger small" data-quarantine-action="all">清空隔离区</button></div><div class="table-wrap spaced"><table><thead><tr><th>选择 / 批次</th><th>文件</th><th>占用</th><th>操作</th></tr></thead><tbody>${rows.map(b=>ui`<tr><td><label class="checkline"><input type="checkbox" data-quarantine-batch="${esc(b.id)}" aria-label="选择批次 ${esc(b.id)}" ${this.selected.has(b.id)?'checked':''}><span>${esc(this.label(b))}${b.irreversible?ui('<small class="sub">已开始永久清理，剩余文件保留</small>'):''}</span></label></td><td>${fmt(b.count)}</td><td>${bytes(b.bytes)}</td><td><div class="buttons"><button class="button small" data-quarantine-files="${esc(b.id)}">查看文件</button>${b.can_restore?ui`<button class="button small" data-undo="${esc(b.id)}">恢复整批</button>`:''}</div></td></tr>`).join('')}</tbody></table></div><div class="buttons spaced"><button class="button small" data-quarantine-page="${this.page-1}" ${this.page===1?'disabled':''}>上一页</button><span>${this.page} / ${count}</span><button class="button small" data-quarantine-page="${this.page+1}" ${this.page===count?'disabled':''}>下一页</button></div>`:empty(tr('隔离区为空'),tr('重复清理后的副本会显示在这里。'))}</section>`;
  },
  async files(id,page=1){
    const data=await api('quarantine?'+new URLSearchParams({batch:id,page})),pages=Math.max(1,Math.ceil(data.total/80));
    openModal(ui`<h2>隔离文件</h2><p class="spaced">共 ${fmt(data.total)} 个文件，显示第 ${data.page} / ${pages} 页。路径相对于当前隔离区。</p>${this.fileList(data.items)}<div class="modal-actions"><button class="button small" data-quarantine-files="${esc(id)}" data-file-page="${data.page-1}" ${data.page===1?'disabled':''}>上一页</button><button class="button small" data-quarantine-files="${esc(id)}" data-file-page="${data.page+1}" ${data.page===pages?'disabled':''}>下一页</button><button class="button primary" data-action="close-modal">关闭</button></div>`);
  },
  fileList(files){
    return `<div class="plan-list spaced">${files.map(f=>`<div class="detail-section"><p class="mono break">${esc(f.rel)}</p><p class="spaced">${bytes(f.size)}${f.original?tr` · 原位置：${esc(areas[f.area]||f.area)} / ${esc(f.original)}`:tr(' · 未关联原位置')}</p></div>`).join('')||ui('<p>该批次已没有隔离文件。</p>')}</div>`;
  },
  showPreview(value){
    this.preview=value.id;
    openModal(ui`<h2>永久清理隔离区</h2><p class="spaced">即将删除 <strong>${fmt(value.count)} 个文件</strong>，文件大小合计 <strong>${bytes(value.bytes)}</strong>，涉及 ${fmt(value.batch_count)} 个批次。</p><div class="notice warning spaced">只删除本次清单中的隔离文件。删除不经过回收站，无法撤销；相关操作记录保留，但不能再整批恢复。</div>${this.fileList(value.files)}${value.count>80?ui('<p class="spaced">清单预览前80个文件，执行范围包含上述批次的全部已预览文件。</p>'):''}<label class="checkline spaced"><input type="checkbox" id="quarantine-confirm">我确认永久删除这些隔离文件</label><div class="modal-actions"><button class="button" data-action="close-modal">取消</button><button class="button danger" data-quarantine-action="confirm" disabled>确认永久删除</button></div>`);
  },
  owns(button){return button.hasAttribute('data-quarantine-action')||button.hasAttribute('data-quarantine-files')||button.hasAttribute('data-quarantine-page');},
  async click(button){
    if(button.dataset.quarantineFiles)return this.files(button.dataset.quarantineFiles,Number(button.dataset.filePage||1));
    if(button.dataset.quarantinePage){this.page=Number(button.dataset.quarantinePage);await render();$('#quarantine-panel')?.scrollIntoView({block:'start'});return;}
    const action=button.dataset.quarantineAction;
    if(action==='jump'){$('#quarantine-panel')?.scrollIntoView({block:'start',behavior:'smooth'});return;}
    if(action==='refresh'){await render();$('#quarantine-panel')?.scrollIntoView({block:'start'});return;}
    if(action==='selected'||action==='all'){
      const job=await startJob('quarantine/preview',action==='all'?{all:true}:{ids:[...this.selected]});
      this.pendingPreview=job.id;return;
    }
    if(action==='confirm'){
      if(!$('#quarantine-confirm')?.checked)return;
      const id=this.preview;await startJob('quarantine/purge',{id,confirmed:true});this.preview=null;this.selected.clear();closeModal();
    }
  },
  change(element){
    if(element.dataset.quarantineBatch){
      const id=element.dataset.quarantineBatch;element.checked?this.selected.add(id):this.selected.delete(id);
      const button=$('[data-quarantine-action=selected]');if(button)button.disabled=!this.selected.size;
    }
    if(element.id==='quarantine-confirm')$('[data-quarantine-action=confirm]').disabled=!element.checked;
  }
};
