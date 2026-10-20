'use strict';
class ModelPicker {
  constructor(api,toast){this.api=api;this.toast=toast;this.rows={codex:[],api:[]};}
  static capabilities={vision:'识图',reasoning:'推理',tool_calling:'工具调用',json_output:'JSON 输出'};
  static effective(m,manual={}){
    const info={...m,...manual};
    const sources={...m?.capability_sources};
    const labels=[...(m?.tags||['识图未确认'])];
    for(const [key,value] of Object.entries(manual)){
      sources[key]={kind:'manual'};
      const title=ModelPicker.capabilities[key];
      const remove=key==='vision'?['识图','仅文字','识图未确认','能力未声明']: [title,'不支持'+title];
      for(let i=labels.length-1;i>=0;i--)if(remove.includes(labels[i]))labels.splice(i,1);
      labels.push(key==='vision'?(value?'识图':'仅文字'):(value?title:'不支持'+title));
    }
    return {...info,tags:labels,capability_sources:sources};
  }
  static capabilityFields(name){
    return ui`<details class="model-capabilities" data-capability-editor="${name}"><summary>能力来源与手动设置</summary><p data-capability-model></p><div data-capability-evidence></div><div class="model-capability-fields">${Object.entries(ModelPicker.capabilities).map(([key,title])=>ui`<label>${tr(title)}<select data-capability="${key}" aria-label="${name==='api_model'?tr('对话模型'):tr('简介生成模型')}${tr(title)}能力"><option value="auto">自动识别</option><option value="true">支持</option><option value="false">不支持</option></select></label>`).join('')}</div><small>按接口地址和完整模型名保存，点击下方“保存设置”生效。同一模型的对话与简介共用能力设置。识图设置决定是否发送图片；其他项标注能力，不会自动添加思考参数或改变调用格式。</small></details>`;
  }
  static endpoint(base){try{const u=new URL(base.trim());return u.origin+u.pathname.replace(/\/+$/,'').replace(/\/chat\/completions$/,'');}catch{return base.trim();}}
  settings(data){
    data.api_profile=this.service;
    if(ModelPicker.endpoint(data.base_url)===this.savedEndpoint)data.api_capability_overrides=this.manual;
  }
  static field(name,title,value,help='',editable=true){
    const api=name.startsWith('api');
    if(api&&editable)return ui`<div class="form-row"><label for="${name}">${esc(title)}</label><div class="model-combo"><input id="${name}" name="${name}" value="${esc(value)}" data-model-picker="api" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${name}-options" autocomplete="off" spellcheck="false" maxlength="2000" placeholder="输入完整模型名或输入部分名称匹配"><button type="button" class="model-toggle" aria-label="展开${esc(title)}列表" aria-controls="${name}-options" aria-expanded="false">▾</button><div id="${name}-options" class="model-options hidden" role="listbox" aria-label="${esc(title)}列表"></div></div><span class="model-tags" data-model-tags="${name}" aria-live="polite"></span><small>${esc(help)} 可输入完整模型名，或输入部分名称匹配列表。</small>${ModelPicker.capabilityFields(name)}</div>`;
    const options=api?[...new Set([value,'deepseek-flash','deepseek-v4-pro'])]:[value];
    return `<label class="form-row"><span>${esc(title)}</span><select name="${name}" data-model-picker="${api?'api':'codex'}">${options.map(id=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(id)}</option>`).join('')}</select><span class="model-tags" data-model-tags="${name}" aria-live="polite"></span>${help?`<small>${esc(help)}</small>`:''}</label>`;
  }
  bind(){
    const form=document.querySelector('#settings-form');if(!form||form.dataset.modelsBound)return;
    form.dataset.modelsBound='true';
    this.rows={codex:[],api:[]};
    const capabilities=JSON.parse(form.dataset.capabilities||'{}');
    this.manual=Object.assign(Object.create(null),capabilities.manual||{});
    this.known=capabilities.known||{};
    this.savedEndpoint=ModelPicker.endpoint(form.dataset.savedBase);
    this.service=form.dataset.apiProfile||'deepseek';this.epoch=0;
    this.profiles=JSON.parse(form.dataset.apiProfiles||'{}');this.drafts={};
    for(const select of form.querySelectorAll('[data-model-picker]'))this.tags(select);
    form.addEventListener('change',e=>{if(e.target.matches('[data-model-picker]'))this.tags(e.target);});
    for(const input of form.querySelectorAll('input[data-model-picker]'))this.bindCombo(input);
    form.addEventListener('change',e=>{
      if(e.target.id==='ai-provider'&&e.target.value!=='codex')this.switchService(e.target.value,form);
      if(!e.target.matches('[data-capability]'))return;
      const editor=e.target.closest('[data-capability-editor]'),input=form.elements[editor.dataset.capabilityEditor];
      const model=input.value.trim();if(!model||e.target.disabled)return;
      const fields={...this.manual[model]},key=e.target.dataset.capability;
      if(e.target.value==='auto')delete fields[key];else fields[key]=e.target.value==='true';
      if(Object.keys(fields).length)this.manual[model]=fields;else delete this.manual[model];
      for(const other of form.querySelectorAll('input[data-model-picker]'))this.tags(other);
    });
    form.addEventListener('input',e=>{
      if(['base_url','api_key','clear_key'].includes(e.target.name)){
        this.rows.api=[];this.known={};
        for(const input of form.querySelectorAll('[data-model-picker=api]')){if(input.tagName==='INPUT')this.hide(input);this.tags(input);}
        this.status(form,'api',tr('连接信息已修改，保存设置后重新读取模型；仍可手动填写完整模型名。'));
      }
    });
    this.load('codex',false,form);
    if(form.elements.provider.value!=='codex'&&form.elements.provider.value!==this.service)this.switchService(form.elements.provider.value,form);
    else this.serviceReady(form);
  }
  serviceReady(form){
    const official=this.service==='deepseek';
    form.elements.base_url.readOnly=official;
    form.querySelector('[data-api-service-note]').textContent=official
      ?tr('DeepSeek 官方 API · 使用专属模型列表。地址、模型和密钥与其他兼容 API 分别保存。')
      :tr('其他 OpenAI 兼容 API · 可填写服务地址及完整模型名，输入部分名称匹配列表。');
    form.elements.api_key.placeholder=form.dataset.hasKey==='true'?tr('已保存，留空保持本服务密钥'):tr('粘贴本服务 API Key');
    if(form.dataset.hasKey==='true')this.load('api',false,form);
    else this.status(form,'api',official?tr('填写并保存 DeepSeek 密钥后读取官方模型列表。'):tr('保存 API 地址和密钥后，会自动读取可用模型；也可直接填写完整模型名。'));
  }
  switchService(service,form){
    if(service===this.service)return;
    this.drafts[this.service]={
      ...Object.fromEntries(['base_url','api_model','api_summary_model','api_context_window','api_key'].map(k=>[k,form.elements[k].value])),
      clear_key:form.elements.clear_key.checked,savedBase:form.dataset.savedBase,has_key:form.dataset.hasKey==='true',
      manual:this.manual,known:this.known,rows:this.rows.api
    };
    const saved=this.profiles[service]||{},draft=this.drafts[service]||{
      ...saved,api_key:'',clear_key:false,savedBase:saved.base_url,
      manual:saved.api_capability_overrides||{},known:saved.api_model_capabilities||{},rows:[]
    };
    this.service=service;this.epoch++;
    this.manual=Object.assign(Object.create(null),draft.manual);this.known=draft.known;this.rows.api=draft.rows;
    form.dataset.savedBase=draft.savedBase;form.dataset.hasKey=String(!!draft.has_key);
    this.savedEndpoint=ModelPicker.endpoint(draft.savedBase);
    for(const name of ['base_url','api_context_window','api_key'])form.elements[name].value=draft[name];
    form.elements.clear_key.checked=draft.clear_key;
    for(const [name,title,help] of [
      ['api_model',tr('对话模型'),tr('用于多轮对话、包管理和预设提示词生成。')],
      ['api_summary_model',tr('简介生成模型'),tr('批量简介与导入分析单独使用；图片能力按此模型判断。')]]){
      form.elements[name].closest('.form-row').outerHTML=ModelPicker.field(name,title,draft[name],help,service==='compatible');
      const input=form.elements[name];
      if(input.tagName==='INPUT')this.bindCombo(input);
      else if(this.rows.api.length)this.selectOptions(input,this.rows.api);
      this.tags(input);
    }
    form.querySelector('[data-refresh-models=api]').disabled=false;
    this.serviceReady(form);
  }
  selectOptions(select,rows){
    const selected=select.value,models=[...rows];
    if(!models.some(m=>m.id===selected))models.unshift({id:selected,name:selected,tags:[tr('当前设置 · 列表未返回')]});
    select.replaceChildren(...models.map(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=`${m.name} · ${m.tags.map(msg).join(' / ')}`;return o;}));
    select.value=selected;
  }
  hide(input){
    input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');
    input.parentElement.querySelector('.model-options').classList.add('hidden');
    input.parentElement.querySelector('.model-toggle').setAttribute('aria-expanded','false');
  }
  suggestions(input,all=false){
    for(const other of input.form.querySelectorAll('input[data-model-picker]'))if(other!==input)this.hide(other);
    const query=all?'':input.value.trim().toLowerCase();
    const rows=this.rows.api.filter(m=>`${m.id} ${m.name}`.toLowerCase().includes(query));
    const list=input.parentElement.querySelector('.model-options');
    list.replaceChildren(...rows.map((m,i)=>{
      const option=document.createElement('div');option.id=`${input.name}-option-${i}`;
      option.setAttribute('role','option');option.setAttribute('aria-selected','false');option.dataset.model=m.id;
      const shown=ModelPicker.effective(m,this.manual[m.id]||{});
      option.textContent=`${m.id}${m.name!==m.id?' · '+m.name:''} · ${shown.tags.map(msg).join(' / ')}`;
      option.title=msg(m.description)||m.id;return option;
    }));
    if(!rows.length){
      const empty=document.createElement('div');empty.className='model-empty';
      empty.textContent=this.rows.api.length?tr('未匹配到模型，可直接使用输入的完整模型名。'):tr('暂无模型列表，可直接填写完整模型名。');
      list.append(empty);
    }
    input.removeAttribute('aria-activedescendant');input.setAttribute('aria-expanded','true');
    input.parentElement.querySelector('.model-toggle').setAttribute('aria-expanded','true');
    list.classList.remove('hidden');
  }
  choose(input,option){
    input.value=option.dataset.model;this.hide(input);this.tags(input);
    input.dispatchEvent(new Event('change',{bubbles:true}));
  }
  bindCombo(input){
    const combo=input.parentElement,list=combo.querySelector('.model-options');
    input.addEventListener('input',()=>{this.tags(input);this.suggestions(input);});
    input.addEventListener('focus',()=>this.suggestions(input,!input.value||this.rows.api.some(m=>m.id===input.value)));
    combo.querySelector('.model-toggle').addEventListener('click',()=>{
      const open=input.getAttribute('aria-expanded')==='true';
      input.focus();if(open)this.hide(input);else this.suggestions(input,true);
    });
    // Keep input focus for mouse selection, while click also works on touch.
    list.addEventListener('mousedown',e=>e.preventDefault());
    list.addEventListener('click',e=>{
      const option=e.target.closest('[role=option]');
      if(option){input.focus();this.choose(input,option);}
    });
    combo.addEventListener('focusout',e=>{if(!combo.contains(e.relatedTarget))this.hide(input);});
    input.addEventListener('keydown',e=>{
      if(e.isComposing)return;
      const open=input.getAttribute('aria-expanded')==='true';
      if(e.key==='Escape'&&open){e.preventDefault();e.stopPropagation();this.hide(input);return;}
      if(['ArrowDown','ArrowUp'].includes(e.key)){
        e.preventDefault();if(!open)this.suggestions(input,true);
        const options=[...list.querySelectorAll('[role=option]')];if(!options.length)return;
        const current=options.findIndex(o=>o.id===input.getAttribute('aria-activedescendant'));
        const next=current<0?(e.key==='ArrowDown'?0:options.length-1):(current+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;
        for(const [i,o] of options.entries())o.setAttribute('aria-selected',String(i===next));
        input.setAttribute('aria-activedescendant',options[next].id);options[next].scrollIntoView({block:'nearest'});
      }else if(e.key==='Enter'&&open){
        e.preventDefault();
        const option=list.querySelector('[aria-selected=true]');
        if(option)this.choose(input,option);else this.hide(input);
      }else if(e.key==='Tab')this.hide(input);
    });
  }
  status(form,provider,message){const el=form.querySelector(`[data-model-status="${provider}"]`);if(el)el.textContent=msg(message);}
  tags(select){
    const model=select.value.trim(),isAPI=select.dataset.modelPicker==='api';
    const sameEndpoint=!isAPI||ModelPicker.endpoint(select.form.elements.base_url.value)===this.savedEndpoint;
    const m=this.rows[select.dataset.modelPicker].find(m=>m.id===model)||(isAPI&&sameEndpoint&&Object.hasOwn(this.known,model)?this.known[model]:null);
    const manual=isAPI&&sameEndpoint?this.manual[model]||{}:{},info=ModelPicker.effective(m,manual);
    const sources=info.capability_sources,labels=info.tags;
    const el=select.form.querySelector(`[data-model-tags="${select.name}"]`);
    el.replaceChildren(...labels.map(tag=>{
      const span=document.createElement('span');span.className='badge'+(tag==='识图'?' green':'');span.textContent=msg(tag);return span;
    }));
    const sourceNames={api:tr('接口声明'),official:tr('官方资料'),manual:tr('手动设置')};
    const kinds=[...new Set(Object.values(sources).map(s=>sourceNames[s.kind]).filter(Boolean))];
    if(kinds.length){
      const source=document.createElement('small');source.className='model-source';source.textContent=tr('来源：')+kinds.join(' / ');el.append(source);
    }
    const editor=select.form.querySelector(`[data-capability-editor="${select.name}"]`);
    if(editor){
      editor.querySelector('[data-capability-model]').textContent=sameEndpoint?tr`当前模型：${model||tr('请先输入完整模型名')}`:tr('接口地址已修改，请先保存地址再设置模型能力。');
      const evidence=editor.querySelector('[data-capability-evidence]');
      evidence.replaceChildren(...Object.entries(ModelPicker.capabilities).map(([key,title])=>{
        const line=document.createElement('p'),source=sources[key];
        const status=info[key]===true?tr('支持'):info[key]===false?tr('不支持'):tr('未确认');
        line.textContent=`${tr(title)}: ${status} · ${sourceNames[source?.kind]||tr('未声明')}`;
        if(source?.kind==='official'&&source.url?.startsWith('https://docs.siliconflow.cn/')){
          const link=document.createElement('a');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';
          link.textContent=tr` 查看依据（${source.checked}核对）`;line.append(link);
        }
        return line;
      }));
      for(const control of editor.querySelectorAll('[data-capability]')){
        control.value=Object.hasOwn(manual,control.dataset.capability)?String(manual[control.dataset.capability]):'auto';
        control.disabled=!sameEndpoint||!model;
      }
    }
    select.title=msg(m?.description)||'';
  }
  async load(provider,refresh=false,form=document.querySelector('#settings-form')){
    if(!form)return;
    const field=provider==='codex'?'codex_path':'base_url',saved=provider==='codex'?form.dataset.savedCodex:form.dataset.savedBase;
    const epoch=this.epoch;
    const unchanged=()=>form.elements[field].value.trim()===saved&&(provider!=='api'||(epoch===this.epoch&&!form.elements.api_key.value&&!form.elements.clear_key.checked));
    if(!unchanged()){this.status(form,provider,tr('地址、密钥或程序路径已修改，请先保存设置再刷新模型。'));return;}
    const button=form.querySelector(`[data-refresh-models="${provider}"]`);
    if(button?.disabled)return;
    if(button)button.disabled=true;
    this.status(form,provider,tr('正在读取可用模型…'));
    try{
      const r=await this.api(`ai-models?provider=${provider}${provider==='api'?'&service='+this.service:''}${refresh?'&refresh=1':''}`);
      if(!form.isConnected||!unchanged())return;
      this.rows[provider]=r.models;
      for(const select of form.querySelectorAll(`[data-model-picker="${provider}"]`)){
        if(select.tagName==='INPUT'){
          this.tags(select);
          if(select.getAttribute('aria-expanded')==='true')this.suggestions(select);
          continue;
        }
        this.selectOptions(select,r.models);this.tags(select);
      }
      this.status(form,provider,r.warning||tr`已读取 ${r.models.length} 个模型${r.cached?tr(' · 使用缓存，可手动刷新'):''}${provider==='codex'?tr(' · 隐藏 / 专用模型可能受账号或用途限制'):''}。`);
      if(refresh)this.toast(r.warning||tr('模型列表已刷新，已保留当前选择。'),!!r.warning);
    }catch(error){if(form.isConnected&&unchanged()){this.status(form,provider,error.message+(provider==='api'?tr(' 仍可直接填写完整模型名。'):''));if(refresh)this.toast(error.message,true);}}
    finally{if(button&&(provider!=='api'||epoch===this.epoch))button.disabled=false;}
  }
}
