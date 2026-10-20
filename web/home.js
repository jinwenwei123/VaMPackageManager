'use strict';
const HOME_CARDS={
  directories:[tr('游戏目录'),tr('快速切换不同版本的 VaM 及对应数据')],library:[tr('资源概览'),tr('资源数量、启停状态和常用分类')],
  favorites:[tr('我的收藏'),tr('收藏数量和资源详情入口')],profiles:[tr('启动方案'),tr('当前方案与创建入口')],
  service:[tr('AI 服务'),tr('服务与模型设置')],generation:[tr('简介与标签'),tr('本轮生成状态和进度')],health:[tr('资源检查'),tr('依赖、同名副本与异常')],
  usage:[tr('API 用量与缓存'),tr('已记录的 tokens 和缓存命中')],appearance:[tr('界面外观'),tr('直接切换配色与明暗')],history:[tr('最近操作'),tr('最近三条文件操作')],
  recent:[tr('最近更新资源'),tr('按文件修改时间展示三个资源')],categories:[tr('资源分类'),tr('全部分类数量与筛选入口')],
  pinned:[tr('固定基础包'),tr('常驻启用资源与详情')],assistant:[tr('对话助手'),tr('当前目录的对话入口')],
  storage:[tr('资源位置与占用'),tr('资源目录、文件数及占用')],index:[tr('索引状态'),tr('最近扫描与内部引用信息')]
};
const HOME_DEFAULT=Object.keys(HOME_CARDS).slice(0,10);
let homeDraft=null,homeDrag='';
function homeVisible(){return (state.homeLayout?.cards||HOME_DEFAULT).filter(key=>HOME_CARDS[key]);}
function homePackages(value,emptyText){return value?.items?.map(p=>`<button class="home-package" data-detail="${esc(p.key)}">${cover(p)}<span><strong>${esc(p.name)}</strong><small>${esc(p.author)} · ${esc(tr(p.category))}</small></span><span class="home-inline-arrow" aria-hidden="true">›</span></button>`).join('')||`<p class="home-empty">${esc(emptyText)}</p>`;}
function homeEditor(){
  const visible=homeVisible();homeDraft={order:[...visible,...Object.keys(HOME_CARDS).filter(k=>!visible.includes(k))],enabled:Object.fromEntries(visible.map(k=>[k,true]))};
  openModal(ui('<h2>自定义主页</h2><p>勾选要显示的卡片，拖动手柄或使用上下按钮调整排列。布局保存在当前游戏目录。</p><form id="home-layout-form"><div id="home-layout-rows"></div><div class="modal-actions"><button type="button" class="button" data-home-default>恢复默认</button><button type="button" class="button" data-action="close-modal">取消</button><button class="button primary" type="submit">保存布局</button></div></form>'));paintHomeEditor();
}
function paintHomeEditor(){
  $('#home-layout-rows').innerHTML=homeDraft.order.map((key,index)=>ui`<div class="home-layout-row" data-home-layout-row="${key}"><span class="home-drag" draggable="true" data-home-drag="${key}" aria-hidden="true" title="拖动${HOME_CARDS[key][0]}排序">⠿</span><label><input type="checkbox" data-home-enabled="${key}" ${homeDraft.enabled[key]?'checked':''}><span><strong>${HOME_CARDS[key][0]}</strong><small>${HOME_CARDS[key][1]}</small></span></label><button type="button" class="button plain small" data-home-move="${key}" data-direction="-1" aria-label="上移${HOME_CARDS[key][0]}" ${index===0?'disabled':''}>↑</button><button type="button" class="button plain small" data-home-move="${key}" data-direction="1" aria-label="下移${HOME_CARDS[key][0]}" ${index===homeDraft.order.length-1?'disabled':''}>↓</button></div>`).join('');
}
function homeEditorClick(button){
  if(button.hasAttribute('data-home-customize')){homeEditor();return true;}
  if(button.hasAttribute('data-home-default')){homeDraft={order:Object.keys(HOME_CARDS),enabled:Object.fromEntries(HOME_DEFAULT.map(k=>[k,true]))};paintHomeEditor();return true;}
  if(button.dataset.homeMove){
    const key=button.dataset.homeMove,index=homeDraft.order.indexOf(key),next=index+Number(button.dataset.direction);
    if(next>=0&&next<homeDraft.order.length){[homeDraft.order[index],homeDraft.order[next]]=[homeDraft.order[next],homeDraft.order[index]];paintHomeEditor();$(`[data-home-move="${key}"][data-direction="${button.dataset.direction}"]`)?.focus();}return true;
  }
  return false;
}
document.addEventListener('dragstart',event=>{const handle=event.target.closest('[data-home-drag]');if(!handle)return;homeDrag=handle.dataset.homeDrag;event.dataTransfer.setData('text/plain',homeDrag);event.dataTransfer.effectAllowed='move';});
document.addEventListener('dragover',event=>{if(homeDrag&&event.target.closest('[data-home-layout-row]')){event.preventDefault();event.dataTransfer.dropEffect='move';}});
document.addEventListener('drop',event=>{const row=event.target.closest('[data-home-layout-row]');if(!homeDrag||!row||!homeDraft)return;event.preventDefault();const from=homeDraft.order.indexOf(homeDrag),to=homeDraft.order.indexOf(row.dataset.homeLayoutRow);if(from>=0&&to>=0){homeDraft.order.splice(from,1);homeDraft.order.splice(to,0,homeDrag);paintHomeEditor();}homeDrag='';});
document.addEventListener('dragend',()=>{homeDrag='';});

// Home reads existing, bounded API views. Browsing never starts a scan or inference.
let homeData={};
const homeSignatures=new WeakMap();
function homeCard(key,title,icon,destination,body,extra=''){
  return ui`<section class="home-card ${extra}" aria-labelledby="home-title-${key}" data-home-card="${key}"><header><h2 id="home-title-${key}"><span class="home-card-icon" aria-hidden="true">${icon}</span>${title}</h2><button class="home-arrow" ${destination} aria-label="打开${title}" title="打开${title}">›</button></header><div class="home-card-body">${body}</div></section>`;
}
function homeRow(label,value,attrs,small=''){
  return `<button class="home-row" ${attrs}><span>${esc(label)}${small?`<small>${esc(small)}</small>`:''}</span><strong>${esc(value)}</strong></button>`;
}
function homeUnavailable(key){return ui`<p class="home-empty">${esc(homeData[key]?.error||tr('暂时无法读取'))}，可点击右上角查看。</p>`;}
function homeCards(){
  const s=state.stats,q=state.summaryQueue||{},c=homeData.settings?.value,profiles=homeData.profiles?.value;
  const active=profiles?.find(p=>p.is_active),favorites=homeData.favorites?.value,missing=homeData.missing?.value;
  const history=homeData.history?.value,usage=state.aiUsage||[];
  const categories=Object.entries(s.categories||{}).sort((a,b)=>b[1]-a[1]).slice(0,5);
  const totals=usage.reduce((a,r)=>{for(const k of Object.keys(a))a[k]+=Number(r[k]||0);return a;},{requests:0,prompt_tokens:0,completion_tokens:0,cache_observed_tokens:0,prompt_cache_hit_tokens:0});
  const rate=totals.cache_observed_tokens?`${(totals.prompt_cache_hit_tokens/totals.cache_observed_tokens*100).toFixed(1)}%`:'—';
  return {
    directories:homeCard('directories',tr('游戏目录'),'⌂','data-home-settings="directories"',
      ui`<span class="home-kicker">当前使用</span><button class="home-name" data-home-settings="directories">${esc(state.workspace?.name||tr('当前目录'))} <span aria-hidden="true">›</span></button><p class="home-note home-path">${esc(state.workspace?.path||s.game||'')}</p><label class="home-kicker spaced" for="home-directory-select">切换已保存的目录</label><select id="home-directory-select" data-workspace-select aria-label="主页切换游戏目录" ${state.busy||state.agentBusy?'disabled':''}>${!state.workspace?.id?ui('<option value="">请选择目录</option>'):''}${(state.directories?.items||[]).map(item=>`<option value="${esc(item.id)}" ${item.id===state.workspace?.id?'selected':''}>${esc(item.name)}</option>`).join('')}</select><p class="home-note">简介、标签、方案、聊天与主页布局一起切换。</p>`),
    recent:homeCard('recent',tr('最近更新资源'),'↗','data-home-library=""',homePackages(homeData.recent?.value,homeData.recent?.error||tr('还没有资源，请先更新索引。'))),
    categories:homeCard('categories',tr('资源分类'),'▦','data-home-library=""',ui`<div class="home-category-list">${(s.categories_all||[]).map(name=>homeRow(tr(name),fmt(s.categories?.[name]),`data-home-category="${esc(name)}"`)).join('')}</div><p class="home-note">分类可交叉，同一个资源可能出现在多个分类中。</p>`),
    pinned:homeCard('pinned',tr('固定基础包'),'♢','data-action="base-packages"',ui`<button class="home-total" data-action="base-packages"><strong>${fmt(s.pinned)}</strong><span>个常驻包</span></button>`+homePackages(homeData.pinned?.value,homeData.pinned?.error||tr('还没有固定基础包，可在资源详情中设置。'))),
    assistant:homeCard('assistant',tr('对话助手'),'✧','data-page="assistant"',ui`<span class="badge">${state.agentBusy?tr('正在处理任务'):tr('等待你的消息')}</span><p class="home-note">继续当前游戏目录的对话，检索资源、整理收藏或查看管理建议。</p><div class="home-bottom"><button class="button small" data-page="assistant">继续对话</button></div>`),
    storage:homeCard('storage',tr('资源位置与占用'),'▣','data-home-settings="directories"',ui`${homeRow(tr('VAR 文件'),fmt(s.files),'data-home-library=""')}${homeRow(tr('资源占用'),bytes(s.bytes),'data-home-library=""')}<p class="home-note home-path">游戏目录：${esc(s.game||'')}</p><p class="home-note home-path">停用库：${esc(s.disabled_folder||'')}</p><div class="home-bottom"><button class="button small" data-home-library="disabled">查看停用资源</button></div>`),
    index:homeCard('index',tr('索引状态'),'↻','data-home-library=""',ui`<span class="home-kicker">上次扫描</span><strong>${esc(s.last_scan||tr('尚未建立索引'))}</strong>${homeRow(tr('含内部引用信息'),fmt(s.deep)+tr(' 个包'),'data-home-library=""')}<p class="home-note">更新索引会发现新包并按当前方案同步启停。深度扫描还读取内部引用。</p><div class="home-bottom"><button class="button small" data-action="scan">更新索引</button><button class="button plain small" data-action="deep-scan">深度扫描</button></div>`),
    library:homeCard('library',tr('资源概览'),'⊞','data-home-library=""',
      ui`<button class="home-total" data-home-library=""><strong>${fmt(s.packages)}</strong><span>个资源包 <span aria-hidden="true">↗</span></span></button><div class="home-metrics">${homeRow(tr('已启用'),fmt(s.active),'data-home-library="active"')}${homeRow(tr('已停用'),fmt(Math.max(0,(s.packages||0)-(s.active||0))),'data-home-library="disabled"')}${homeRow(tr('资源占用'),bytes(s.bytes),'data-home-library=""')}</div><div class="home-chips">${categories.map(([name,count])=>`<button data-home-category="${esc(name)}">${esc(tr(name))} <b>${fmt(count)}</b></button>`).join('')}</div><p class="home-note">${s.last_scan?tr('上次扫描 ')+esc(s.last_scan):tr('尚未建立索引，可使用顶部“更新索引”。')}</p>`),
    favorites:homeCard('favorites',tr('我的收藏'),'★','data-action="favorites"',
      ui`<button class="home-total" data-action="favorites"><strong>${fmt(s.favorites)}</strong><span>个收藏 <span aria-hidden="true">↗</span></span></button><div class="home-package-list">${favorites?favorites.items.map(p=>`<button class="home-package" data-detail="${esc(p.key)}">${cover(p)}<span><strong>${esc(p.name)}</strong><small>${esc(p.author)} · ${esc(tr(p.category))}</small></span><span class="home-inline-arrow" aria-hidden="true">›</span></button>`).join('')||ui('<p class="home-empty">在资源库点亮星标，把喜欢的资源放在这里。</p>'):homeUnavailable('favorites')}</div>`),
    profiles:homeCard('profiles',tr('启动方案'),'▷','data-page="profiles"',
      profiles?ui`<span class="home-kicker">当前方案</span><button class="home-name" data-page="profiles">${esc(active?profileDisplayName(active):tr('未启用已保存方案'))} <span aria-hidden="true">›</span></button><p class="home-note">${active?tr`${fmt(active.keys.length)} 个主包 · 含依赖 ${fmt(active.included_count)} 个包`:tr('到启动方案选择主包组合，预览后启用。')}</p>${homeRow(tr('固定基础包'),fmt(s.pinned),'data-action="base-packages"')}${homeRow(tr('已保存方案'),fmt(profiles.length),'data-page="profiles"')}<div class="home-bottom"><button class="button small" data-action="source-profile">从收藏 / 文件夹创建</button><button class="button plain small" data-page="profiles">管理方案</button></div>`:homeUnavailable('profiles')),
    service:homeCard('service',tr('AI 服务'),'✧','data-home-settings="provider"',
      c?ui`<span class="home-kicker">当前使用</span><button class="home-name" data-home-settings="provider">${esc({codex:'Codex',deepseek:'DeepSeek API',compatible:tr('兼容 API')}[c.provider]||c.provider)} <span aria-hidden="true">›</span></button>${homeRow(tr('对话模型'),c.provider==='codex'?c.codex_model:c.api_model,`data-home-settings="${c.provider==='codex'?'codex_model':'api_model'}"`)}${homeRow(tr('简介与标签'),c.provider==='codex'?c.codex_summary_model:c.api_summary_model,`data-home-settings="${c.provider==='codex'?'codex_summary_model':'api_summary_model'}"`)}<p class="home-note">${c.provider==='codex'?tr('沿用 Codex CLI 登录账号'):c.has_key?tr('已保存 API 密钥'):tr('尚未填写 API 密钥')} · 点击模型可修改设置</p><div class="home-bottom"><button class="button small" data-page="assistant">打开对话助手</button></div>`:homeUnavailable('settings')),
    generation:homeCard('generation',tr('简介与标签'),'◇','data-home-library=""',
      ui`<div class="home-status"><span class="badge">${esc({running:tr('正在生成'),pausing:tr('准备暂停'),paused:tr('已暂停'),error:tr('等待继续'),done:tr('本轮完成'),cancelled:tr('本轮已结束')}[q.status]||tr('尚未开始'))}</span><span>${esc({both:tr('简介＋标签'),summary:tr('仅简介'),tags:tr('仅标签')}[q.target]||'')}</span></div><button class="home-generation-count" data-home-library=""><strong>${fmt(q.updated)}</strong><span>本轮已生成</span></button><p class="home-note">${q.status?tr`已处理 ${fmt(q.current)} / ${fmt(q.total)} · 跳过 ${fmt(q.skipped)}`:tr('为资源添加简介和内容标签，方便搜索与推荐。')}</p>${q.error?`<p class="home-note home-error">${esc(msg(q.error))}</p>`:''}<div class="home-bottom"><button class="button small" data-home-library="">查看生成进度</button><button class="button plain small" data-home-library="unlabeled">未生成简介</button></div>`),
    health:homeCard('health',tr('资源检查'),'⛓','data-page="dependencies"',
      ui`${homeRow(tr('缺失依赖'),missing?fmt(missing.total):tr('暂不可用'),'data-page="dependencies"',tr('按现有索引核对'))}${homeRow(tr('同名多余副本'),fmt(s.duplicates),'data-page="duplicates"')}${homeRow(tr('读取异常'),fmt(s.errors),'data-home-library="errors"')}${homeRow(tr('资源提示'),fmt(s.warnings),'data-home-library="warnings"')}<p class="home-note">点击项目查看详情；清理和修复沿用原有预览流程。</p>`),
    usage:homeCard('usage',tr('API 用量与缓存'),'◴','data-home-settings="usage"',
      ui`<div class="home-usage"><button data-home-settings="usage"><strong>${rate}</strong><span>累计缓存命中率</span></button><button data-home-settings="usage"><strong>${fmt(totals.requests)}</strong><span>已记录请求</span></button></div><div class="home-metrics">${homeRow(tr('输入 tokens'),fmt(totals.prompt_tokens),'data-home-settings="usage"')}${homeRow(tr('输出 tokens'),fmt(totals.completion_tokens),'data-home-settings="usage"')}</div><p class="home-note">仅汇总已记录的 API 请求；命中率按服务返回的可统计输入计算，不含 Codex CLI 用量。</p>`),
    appearance:homeCard('appearance',tr('界面外观'),'◐','data-page="settings"',
      ui`<span class="home-kicker">主题配色</span><div class="home-palettes" role="group" aria-label="主页主题配色">${VaMPMTheme.palettes.map(([value,label])=>`<button data-palette-choice="${value}" title="${label}" aria-label="${label}" aria-pressed="false"><span class="theme-swatch swatch-${value}" aria-hidden="true"><i></i><i></i><i></i></span></button>`).join('')}</div><span class="home-kicker">明暗模式</span><div class="theme-modes home-modes" role="group" aria-label="主页明暗模式">${VaMPMTheme.modes.map(([value,label])=>`<button data-mode-choice="${value}" aria-pressed="false">${label}</button>`).join('')}</div><p class="home-note">即点即换，自动保存。</p>`),
    history:homeCard('history',tr('最近操作'),'↶','data-page="history"',
      history?history.slice(0,3).map(r=>homeRow(kinds[r.kind]||r.kind,{done:tr('已完成'),undone:tr('已恢复'),interrupted:tr('待恢复'),running:tr('进行中'),undoing:tr('恢复中断'),purging:tr('清理未完成'),partial:tr('部分清理')}[r.status]||r.status,'data-page="history"',tr`${r.created} · ${fmt(r.count)} 个文件`)).join('')||ui('<p class="home-empty">还没有文件操作。后续可在这里查看记录和恢复入口。</p>'):homeUnavailable('history'))
  };
}
async function homePage({id,signal}){
  const choices={service:['settings','settings'],profiles:['profiles','profiles'],favorites:['favorites','packages?scope=favorites&sort=new&limit=3&page=1'],health:['missing','missing?page=1'],history:['history','history'],recent:['recent','packages?sort=new&limit=3&page=1'],pinned:['pinned','packages?state=pinned&limit=3&page=1']};
  const routes=Object.fromEntries(homeVisible().filter(key=>choices[key]).map(key=>choices[key]));
  const keys=Object.keys(routes),results=await Promise.allSettled(keys.map(k=>api(routes[k],undefined,{signal})));
  if(id!==state.renderId)return '';
  homeData=Object.fromEntries(keys.map((k,i)=>[k,results[i].status==='fulfilled'?{value:results[i].value}:{error:tr('暂时无法读取')}]));
  const cards=homeCards();
  return heading(tr('主页'),tr('资源、方案与 AI，常用信息一眼可见。'),ui`<form id="home-search" class="home-search"><label class="sr-only" for="home-search-input">主页搜索</label><input id="home-search-input" name="q" placeholder="搜索资源库…" autocomplete="off"><button type="submit" aria-label="执行搜索">⌕</button></form><button class="button small" data-home-customize>自定义主页</button>`)+`<div class="home-grid">${homeVisible().map(key=>cards[key]).join('')}</div>`;
}
function paintHome(){
  const grid=$('.home-grid');if(state.page!=='home'||!grid)return;
  const cards=homeCards(),visible=homeVisible();
  for(const card of [...grid.children])if(!visible.includes(card.dataset.homeCard))card.remove();
  visible.forEach((key,index)=>{
    const html=cards[key];let card=$(`[data-home-card="${key}"]`,grid);
    if(!card||homeSignatures.has(card)&&homeSignatures.get(card)!==html){
      const template=document.createElement('template');template.innerHTML=html;
      const next=template.content.firstElementChild;if(card)card.replaceWith(next);card=next;
    }
    if(grid.children[index]!==card)grid.insertBefore(card,grid.children[index]||null);
    homeSignatures.set(card,html);
  });
  if(!visible.length)grid.innerHTML=ui('<div class="home-empty-layout"><p>主页还没有卡片，选择你需要的信息。</p><button class="button" data-home-customize>添加卡片</button></div>');
  covers.watch();VaMPMTheme.paint();
}
async function homeLibrary(filter='',category='',query=''){
  state.query={q:query,category,state:filter,scope:'',sort:state.query.sort,page:1};
  await changePage('library');
}
async function homeSettings(target){
  await changePage('settings');
  if(state.page!=='settings')return;
  const element=target==='directories'?$('#game-directories'):target==='usage'?$('#ai-usage'):$('#settings-form [name="'+target+'"]');
  element?.scrollIntoView({block:'center',behavior:'instant'});element?.focus({preventScroll:true});
}
