'use strict';
class ChatPresets {
  constructor(api,startJob,toast,fill){
    this.api=api;this.startJob=startJob;this.toast=toast;this.fill=fill;
    this.value={items:[],revision:0,max_featured:5};this.editor=null;this.pending=null;this.saving=false;
    this.dialog=document.querySelector('#preset-dialog');
    this.dialog.addEventListener('close',()=>{this.cancelDraft();this.editor=null;});
    this.dialog.addEventListener('click',e=>{if(e.target===this.dialog)this.dialog.close();});
    this.dialog.addEventListener('submit',e=>{if(e.target.id==='preset-form'){e.preventDefault();this.saveForm().catch(error=>this.toast(error.message,true));}});
    this.dialog.addEventListener('change',e=>{if(e.target.dataset.presetFeature)this.feature(e.target).catch(error=>this.toast(error.message,true));});
  }
  async load(){this.value=await this.api('chat-presets');return this.value;}
  count(){return this.value.items.filter(p=>p.featured).length;}
  bar(){
    const featured=this.value.items.filter(p=>p.featured).slice(0,5);
    return ui`<div class="chat-suggestions" aria-label="常用对话预设"><div class="chat-preset-shortcuts">${featured.length?featured.map(p=>`<button type="button" data-preset-use="${esc(p.id)}" title="${esc(p.title)}">${esc(p.title)}</button>`).join(''):ui('<span class="muted">选择常用预设，快速开始对话</span>')}</div><button type="button" class="chat-presets-more" data-preset-action="open" aria-label="查看全部对话预设" title="全部预设与管理"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4"/></svg></button></div>`;
  }
  paintBar(){const bar=document.querySelector('.chat-suggestions');if(bar)bar.outerHTML=this.bar();}
  header(title,sub,actions){return ui`<header class="preset-header"><div><h2 id="preset-heading">${title}</h2><p>${sub}</p></div><div class="buttons">${actions}<button type="button" class="button plain icon" data-preset-action="close" aria-label="关闭预设面板">×</button></div></header>`;}
  async open(){await this.load();this.editor=null;this.renderList();this.paintBar();if(!this.dialog.open)this.dialog.showModal();}
  renderList(){
    const scroll=this.dialog.querySelector('.preset-scroll')?.scrollTop||0;
    const count=this.count(),full=count>=5;
    this.dialog.innerHTML=this.header(tr('对话预设'),tr`常用栏 ${count} / 5 · 共 ${this.value.items.length} 个`,ui('<button type="button" class="button primary small" data-preset-action="new">＋ 新增预设</button>'))+
      ui`<div class="preset-scroll"><p class="preset-help">点击标题填入对话框。勾选“常用”可放到下方快捷栏，最多 5 个；选满后先取消一个再替换。</p>${this.value.warning?`<div class="notice warning">${esc(msg(this.value.warning))}</div>`:''}<div class="preset-list">${this.value.items.map(p=>ui`<article class="preset-row" data-preset-id="${esc(p.id)}"><button type="button" class="preset-use" data-preset-use="${esc(p.id)}" title="点击填入对话框"><strong>${esc(p.title)}</strong><span>${esc(p.prompt)}</span></button><div class="preset-row-actions"><label class="checkline"><input type="checkbox" data-preset-feature="${esc(p.id)}" aria-label="常用：${esc(p.title)}" ${p.featured?'checked':''} ${full&&!p.featured?'disabled':''}>常用</label><button type="button" class="button plain small" data-preset-action="edit" data-id="${esc(p.id)}">编辑</button><button type="button" class="button plain danger small" data-preset-action="delete" data-id="${esc(p.id)}">删除</button></div><div class="preset-delete hidden"><span>删除这个预设？</span><button type="button" class="button small" data-preset-action="keep">取消</button><button type="button" class="button small danger" data-preset-action="confirm-delete" data-id="${esc(p.id)}">确认删除</button></div></article>`).join('')||ui('<div class="empty"><h3>还没有预设</h3><p>点击“新增预设”，填写标题和提示词。</p></div>')}</div></div>`;
    this.dialog.querySelector('.preset-scroll').scrollTop=scroll;
  }
  edit(id){
    const found=id?this.value.items.find(p=>p.id===id):null;
    if(id&&!found)throw Error(tr('该预设已不存在，请重新打开列表。'));
    this.editor=found?{...found}:{id:null,title:'',prompt:'',featured:false};
    const p=this.editor;
    this.dialog.innerHTML=this.header(id?tr('编辑预设'):tr('新增预设'),tr('填写标题和提示词，保存后可在全部预设中使用。'),ui('<button type="button" class="button small" data-preset-action="back">返回列表</button>'))+
      ui`<div class="preset-scroll"><form id="preset-form"><label class="form-row"><span>标题</span><input name="title" required maxlength="60" value="${esc(p.title)}" placeholder="例如：推荐自然风格人物卡"></label><div class="preset-prompt-label"><label for="preset-prompt">提示词</label><button type="button" class="button small" data-preset-action="generate">✧ AI 生成提示词</button></div><textarea id="preset-prompt" name="prompt" rows="10" required maxlength="12000" placeholder="描述你希望助手完成的任务，也可先填写标题再用 AI 生成。">${esc(p.prompt)}</textarea><p class="preset-help">AI 使用设置中已保存的对话模型，生成结果只放入这里；你可以修改后再保存。</p><div class="preset-draft-status" role="status"></div><button type="button" class="button danger small hidden" data-preset-action="stop">停止生成</button><label class="checkline spaced"><input name="featured" type="checkbox" ${p.featured?'checked':''} ${this.count()>=5&&!p.featured?'disabled':''}>显示在常用栏${this.count()>=5&&!p.featured?tr('（已满 5 个，可稍后在列表替换）'):''}</label><div class="modal-actions"><button type="button" class="button" data-preset-action="back">取消</button><button type="submit" class="button primary">保存预设</button></div></form></div>`;
    this.dialog.querySelector('[name=title]').focus();
  }
  controls(disabled){
    const form=this.dialog.querySelector('#preset-form');if(!form)return;
    for(const el of form.querySelectorAll('input,textarea,button'))el.disabled=disabled;
    const featured=form.elements.featured;
    if(!disabled)featured.disabled=this.count()>=5&&!this.editor?.featured;
    const stop=form.querySelector('[data-preset-action=stop]');stop.disabled=false;stop.classList.toggle('hidden',!disabled||!this.pending);
  }
  async mutate(data){
    this.value=await this.api('chat-presets',{...data,revision:this.value.revision});this.paintBar();
  }
  async feature(input){
    const id=input.dataset.presetFeature,featured=input.checked;input.disabled=true;
    try{await this.mutate({action:'feature',id,featured});this.renderList();}
    catch(error){input.checked=!featured;input.disabled=false;throw error;}
  }
  async saveForm(){
    const form=this.dialog.querySelector('#preset-form');if(!form||this.saving||this.pending||!form.reportValidity())return;
    const data={action:'save',id:this.editor.id,title:form.elements.title.value,prompt:form.elements.prompt.value,featured:form.elements.featured.checked};
    this.saving=true;this.controls(true);
    try{await this.mutate(data);this.editor=null;this.renderList();this.toast(tr('预设已保存'));}
    finally{this.saving=false;this.controls(false);}
  }
  async generate(){
    const form=this.dialog.querySelector('#preset-form');if(!form||this.pending||this.saving)return;
    if(!form.elements.title.reportValidity())return;
    const data={title:form.elements.title.value,prompt:form.elements.prompt.value};
    const pending={id:null};this.pending=pending;this.controls(true);
    this.dialog.querySelector('.preset-draft-status').textContent=tr('正在使用对话模型生成…');
    try{
      const job=await this.startJob('chat-presets/draft',data);pending.id=job.id;
      if(this.pending!==pending)await this.api('cancel',{id:job.id});
    }catch(error){if(this.pending===pending){this.pending=null;this.controls(false);this.dialog.querySelector('.preset-draft-status').textContent=error.message;}throw error;}
  }
  cancelDraft(){
    const pending=this.pending;this.pending=null;this.controls(false);
    if(pending?.id)this.api('cancel',{id:pending.id}).catch(()=>{});
    const status=this.dialog.querySelector('.preset-draft-status');if(status&&pending)status.textContent=tr('已停止生成，原提示词保留。');
  }
  draftResult(job){
    if(!this.pending||this.pending.id!==job.id)return;
    this.pending=null;this.controls(false);
    const form=this.dialog.querySelector('#preset-form');if(!form||!this.dialog.open)return;
    const status=this.dialog.querySelector('.preset-draft-status');
    if(job.status==='done'&&job.result?.preset_prompt){form.elements.prompt.value=job.result.preset_prompt;status.textContent=tr`已生成 · ${job.result.model}，可修改后保存。`;}
    else status.textContent=msg(job.error)||tr('生成未完成，原提示词保留。');
  }
  owns(button){return !!(button.dataset.presetAction||button.dataset.presetUse);}
  async click(button){
    const use=button.dataset.presetUse,action=button.dataset.presetAction;
    if(use){const p=this.value.items.find(p=>p.id===use);if(!p)throw Error(tr('预设已不存在，请刷新列表。'));if(this.dialog.open)this.dialog.close();this.fill(p.prompt);return;}
    if(action==='open')return this.open();
    if(action==='close')return this.dialog.close();
    if(action==='new')return this.edit();
    if(action==='edit')return this.edit(button.dataset.id);
    if(action==='back'){this.cancelDraft();await this.load();this.editor=null;this.renderList();this.paintBar();return;}
    if(action==='generate')return this.generate();
    if(action==='stop')return this.cancelDraft();
    if(action==='delete'){button.closest('.preset-row').querySelector('.preset-delete').classList.remove('hidden');return;}
    if(action==='keep'){button.closest('.preset-delete').classList.add('hidden');return;}
    if(action==='confirm-delete'){
      button.disabled=true;
      try{await this.mutate({action:'delete',id:button.dataset.id});this.renderList();this.toast(tr('预设已删除'));}
      finally{button.disabled=false;}
    }
  }
}
