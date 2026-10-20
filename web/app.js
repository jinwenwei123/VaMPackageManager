'use strict';
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => Number(v||0).toLocaleString(VaMPMI18n.locale);
const bytes = v => {let n=Number(v||0),u=0;while(n>=1024&&u<4){n/=1024;u++;}return `${n.toFixed(u? n>=100?0:1:0)} ${['B','KiB','MiB','GiB','TiB'][u]}`;};
const safeLink = url => {try {const u=new URL(url);return ['https:','http:'].includes(u.protocol)?esc(u.href):'#';}catch{return '#';}};
const names = {home:tr('主页'),library:tr('资源库'),assistant:tr('对话助手'),dependencies:tr('依赖检查'),duplicates:tr('重复清理'),profiles:tr('启动方案'),history:tr('操作记录'),settings:tr('AI 与设置')};
const LIBRARY_PAGE_SIZE=42;
const glyph = {'场景':'▧','人物':'♙','服饰':'♧','头发':'≋','皮肤纹理':'◈','变形':'◇','插件':'⌘','环境道具':'⬡','姿势动作':'↗','其他':'□'};
const kinds = {purge_quarantine:tr('永久清理隔离区'),repair_names:tr('修复包文件名'),repair_json:tr('修复包内 JSON'),pin:tr('固定基础包'),unpin:tr('解除固定'),sync_base:tr('同步基础依赖'),stage_all:tr('全部停用并分类整理'),sync_favorites:tr('同步默认收藏方案'),sync_activation:tr('按当前方案同步启停'),rename_collection:tr('重命名收藏目录'),favorite:tr('收藏并归档'),unfavorite:tr('取消收藏并移回'),dedup:tr('清理重复副本'),organize:tr('按分类归档'),move:tr('转移到指定文件夹'),activate:tr('切换按需启用方案'),restore_all:tr('恢复全部启用')};
const areas = {active:'AddonPackages',vault:tr('停用库'),quarantine:tr('隔离区')};
let token=location.hash.slice(1)||sessionStorage.getItem('vampm-token')||'';
if(token){sessionStorage.setItem('vampm-token',token);history.replaceState(null,'',location.pathname);}
const covers=new PackageCovers(token);
const pageConnection=new PageConnection(token);
pageConnection.start();
const pagerDock=new PagerDock();
const selectionDock=new SelectionDock();
const modelPicker=new ModelPicker(api,toast);
const summaries=new PackageSummaries();
const contentTags=new PackageTags();
const chatComposer=new ChatComposerResize();
const chatPresets=new ChatPresets(api,startJob,toast,prompt=>{chat.draft=prompt;const input=$('#chat-input');if(input){input.value=prompt;input.focus();}});
let preferencesLoaded=false,preferenceWrites=Promise.resolve();
let selectionStorageKey='vampm-selection';
let savedSelection=[];try{savedSelection=JSON.parse(localStorage.getItem('vampm-selection')||'[]');}catch{}
const state={page:sessionStorage.getItem('vampm-language-page')||'home',stats:{categories:{}},query:{q:'',category:'',state:'',scope:'',sort:'name',page:1},selected:new Set(savedSelection),
    view:'list',revision:null,items:[],itemsQuery:'',total:0,seenJobs:new Set(),busy:false,summaryQueue:{},aiUsage:[],plan:null,depPage:1,depQ:'',sources:null,detail:null,renderId:0,selectionPage:1,profileName:''};
sessionStorage.removeItem('vampm-language-page');
let toastTimer,searchTimer,renderController,pollTimer,polling=false,stopped=false,navSignature='',saveTimer,detailRequest=0;
const chat={id:'',seq:0,rows:new Map(),busy:false,draft:'',readOnly:false,refreshing:false,context:null};
const toolNames={search_packages:tr('检索资源'),inspect_package:tr('查看资源详情'),check_dependencies:tr('检查依赖'),manage_profiles:tr('管理启动方案'),plan_files:tr('预览文件操作'),apply_plan:tr('执行文件操作'),library_task:tr('资源管理任务'),get_status:tr('读取资源库状态'),label_package:tr('整理分类与简介'),show_packages:tr('展示推荐卡片'),list_folders:tr('查看文件夹'),set_favorites:tr('管理收藏')};

async function api(path,data,options={}){
  const res=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Session-Token':token,'Content-Type':'application/json',...(window.VaMPMWorkspace?.epoch?{'X-Workspace-Epoch':window.VaMPMWorkspace.epoch}:{})},...(data===undefined?{}:{body:JSON.stringify(data)}),...options});
  const out=await res.json();if(out.workspace_changed){reloadWorkspace();throw new DOMException(tr('目录已切换'),'AbortError');}if(!res.ok)throw Error(msg(out.error)||tr('请求失败'));return out;
}
function toast(text,error=false){const e=$('#toast');e.textContent=text;e.className='toast'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>e.classList.add('hidden'),error?9000:4200);}
function saveSelection(){try{localStorage.setItem(selectionStorageKey,JSON.stringify([...state.selected]));}catch{}}
function persist(){clearTimeout(saveTimer);saveTimer=setTimeout(saveSelection,50);}
function rememberLibrary(patch){
  preferenceWrites=preferenceWrites.catch(()=>{}).then(()=>api('library-preferences',patch,{keepalive:true}));
  preferenceWrites.catch(error=>toast(tr('浏览偏好未能保存：')+error.message,true));
}
function loadLibraryPreferences(value){
  if(preferencesLoaded)return;preferencesLoaded=true;
  state.view=value?.view==='grid'?'grid':'list';
  state.query.sort=['name','new','size'].includes(value?.sort)?value.sort:'name';
}
window.addEventListener('beforeunload',saveSelection);
function favoriteView(q=state.query){return q.scope==='favorites'||q.state==='favorite';}
function querySignature(q=state.query){return JSON.stringify([q.q,q.category,q.state,q.scope||'',q.sort,q.page]);}
function nav(){
  const signature=JSON.stringify([state.page,state.query.state,state.query.scope,state.stats.packages,state.stats.favorites,state.stats.last_scan]);
  if(signature===navSignature)return;navSignature=signature;
  const icons={library:'⊞',assistant:'✧',dependencies:'⛓',duplicates:'▱',profiles:'▷',history:'↶'};
  $('#navigation').innerHTML=Object.entries(icons).map(([k,i])=>`<button class="nav-item ${state.page===k?'active':''}" data-page="${k}"><span>${i}</span>${names[k]}${k==='library'?`<span class="count">${fmt(state.stats.packages)}</span>`:''}</button>`).join('');
  $('#navigation').insertAdjacentHTML('afterbegin',ui`<button class="nav-item favorite-nav ${state.page==='library'&&favoriteView()?'active':''}" data-action="favorites"><span>★</span>收藏夹<span class="count">${fmt(state.stats.favorites)}</span></button>`);
  $('#navigation').insertAdjacentHTML('afterbegin',ui`<button class="nav-item ${state.page==='home'?'active':''}" data-page="home"><span>⌂</span>主页</button>`);
  if(state.page==='library'&&favoriteView())$('#navigation [data-page=library]').classList.remove('active');
  $('[data-page="settings"]').classList.toggle('active',state.page==='settings');
  $('#breadcrumb').textContent=state.page==='library'&&favoriteView()?tr('收藏夹'):names[state.page];
  $('#scan-time').textContent=state.stats.last_scan?tr('上次扫描 ')+state.stats.last_scan.slice(5,16):tr('尚未建立索引');
}
function heading(title,sub,actions=''){return `<div class="page-head"><div><div class="eyebrow">YOUR LOCAL COLLECTION</div><h1>${title}</h1><p>${sub}</p></div><div class="buttons">${actions}</div></div>`;}
function empty(title,text,action=''){return `<div class="empty"><div class="empty-icon">⊞</div><h2>${title}</h2><p>${text}</p>${action}</div>`;}
function stat(label,value,note,icon,featured=false){return `<div class="stat ${featured?'featured':''}"><div class="stat-label">${label}</div><div class="stat-value">${value}</div><div class="stat-note">${note}</div><span class="stat-icon">${icon}</span></div>`;}
function pages(page,total,limit,action){
  const count=Math.max(1,Math.ceil(total/limit));page=Math.min(count,Math.max(1,page));
  const button=(label,path,value,disabled)=>`<button type="button" class="pager-button" data-action="${action}" data-value="${value}" aria-label="${label}" title="${label}" ${disabled?'disabled':''}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg></button>`;
  return ui`<nav class="pagination-slot" aria-label="分页" data-pager-action="${action}" data-current="${page}" data-count="${count}" ${action!=='selection-page'?'data-floating-pager':''}><div class="pagination">
    ${button(tr('第一页'),'M6 5v14M17 6l-6 6 6 6',1,page<=1)}${button(tr('上一页'),'m15 6-6 6 6 6',page-1,page<=1)}
    <form class="pager-jump" data-pager-form="${action}"><input aria-label="跳转页码" title="输入页码，按 Enter 跳转" inputmode="numeric" autocomplete="off" spellcheck="false" maxlength="7" value="${page}" ${count===1?'disabled':''}><span aria-hidden="true">/ ${fmt(count)}</span><span class="sr-only" aria-live="polite">${page} / ${count} 页 · ${fmt(total)} 项</span></form>
    ${button(tr('下一页'),'m9 6 6 6-6 6',page+1,page>=count)}${button(tr('最后一页'),'M18 5v14M7 6l6 6-6 6',count,page>=count)}
  </div></nav>`;
}
async function changePagination(action,value){
  const slot=$(`[data-pager-action="${action}"]`);if(!slot)return;
  const count=Number(slot.dataset.count),current=Number(slot.dataset.current),raw=String(value).trim();
  if(!/^\d+$/.test(raw))throw Error(tr`请输入 1～${count} 的整数页码。`);
  const next=Math.min(count,Math.max(1,Number(raw)));
  if(next===current){$('input',slot).value=String(current);return;}
  const page=state.page,id=state.renderId+1;
  if(action==='library-page')state.query.page=next;
  else if(action==='dep-page')state.depPage=next;
  else if(action==='selection-page')state.selectionPage=next;
  await render();
  if(state.page!==page||state.renderId!==id)return;
  const target=action==='library-page'?$('#library-results'):action==='dep-page'?$('#missing-panel'):$('.selected-list');
  if(action==='selection-page'){if(target)target.scrollTop=0;}
  else target?.scrollIntoView({block:'start',behavior:'instant'});
  pagerDock.update();
}
function cover(p,extra=''){return ui`<div class="package-art ${extra}" data-type="${esc(p.category)}" data-cover="${esc(p.key)}" data-cover-version="${esc(p.mtime)}-${p.size}" data-cover-label="${esc(tr`${p.name} 封面`)}">${glyph[p.category]||'□'}</div>`;}
function favoriteButton(p){return `<button class="favorite ${p.favorite?'on':''}" data-favorite="${esc(p.key)}" data-value="${!p.favorite}" aria-label="${p.favorite?tr('取消收藏'):tr('收藏')}" title="${p.favorite?p.pinned?tr('更新收藏标记，固定文件保持原位'):tr('取消收藏并归入普通分类'):p.pinned?tr('更新收藏标记，固定文件保持原位'):tr('收藏并移入收藏文件夹')}">${p.favorite?'★':'☆'}</button>`;}
function warningBadge(p){return (p.pinned?ui`<span class="pin-mark" title="固定基础包 · 常驻启用" aria-label="固定基础包">♢ 固定</span>`:'')+(p.warnings?.length?ui`<button class="warning-mark" data-detail="${esc(p.key)}" title="${esc(p.warnings.map(msg).join('; '))}" aria-label="查看资源提示">!</button>`:'');}
function categoryBadge(p,all=false){
  const categories=p.categories?.length?p.categories:[p.category],visible=all?categories:categories.slice(0,3);
  return ui`<div class="category-badges ${all?'detail-categories':''}" title="${esc(categories.map(tr).join(' · '))} · 主分类：${esc(tr(p.category))}">${visible.map(c=>ui`<span class="package-category" data-type="${esc(c)}" aria-label="分类：${esc(tr(c))}"><span aria-hidden="true">${glyph[c]||'□'}</span><span class="category-name">${esc(tr(c))}</span></span>`).join('')}${categories.length>visible.length?ui`<span class="category-more" aria-label="更多分类：${esc(categories.slice(visible.length).map(tr).join(' / '))}">+${categories.length-visible.length}</span>`:''}</div>`;
}
function summaryOptions(scope,keys=[]){
  const label={all:tr('全部资源'),favorites:tr('全部收藏'),selected:tr`所选 ${keys.length} 个包`}[scope];
  state.summaryRequest={scope,...(scope==='selected'?{keys:[...keys]}:{})};
  openModal(ui`<h2>一键生成 AI 简介与标签</h2><p>本次范围：<strong>${esc(label)}</strong>${scope==='selected'?'':tr('，包含停用包，不受搜索、分类或当前页筛选影响')}。</p><div class="summary-targets" role="radiogroup" aria-label="生成内容"><label><input type="radio" name="summary-target" value="both" checked>简介＋标签</label><label><input type="radio" name="summary-target" value="summary">仅简介</label><label><input type="radio" name="summary-target" value="tags">仅标签</label></div><div class="summary-options" role="radiogroup" aria-label="生成方式"><label class="summary-choice"><input type="radio" name="summary-mode" value="all" checked><span><strong>全部生成 / 重新生成</strong><small>为范围内所有可读取的包生成所选内容。选择简介会更新简介和主分类；仅标签不改变已有简介和分类。</small></span></label><label class="summary-choice"><input type="radio" name="summary-mode" value="missing"><span><strong>仅补充未生成</strong><small>简介和标签分别判断完成状态，只补充缺失或包内容已变化的部分，保留已生成内容。</small></span></label></div><p>Codex 每批最多 10 个，API 每批最多 5 个，可暂停和继续；使用设置中简介生成模型的额度。失败时保留旧简介，已保存的结果不会在本轮续跑时重复生成。</p><div class="modal-actions"><button class="button" data-action="close-modal">取消</button><button class="button primary" data-action="confirm-summary">确认开始</button></div>`);
}
function summaryQueuePanel(){
  const q=state.summaryQueue||{},pending=['running','pausing','paused','error'].includes(q.status),scope={all:tr('全部资源'),favorites:tr('收藏'),selected:tr('所选资源')}[q.scope]||'';
  if(!q.status||q.status==='cancelled'||(q.status==='done'&&q.notice_dismissed))return ui`<div class="summary-queue-note">为${favoriteView()?tr('全部收藏'):tr('全部资源')}生成简介或内容标签，点击后选择全部重新生成或仅补充未生成。范围不受搜索或分类筛选影响。</div>`;
  return ui`<div class="summary-queue-card ${q.status==='error'?'has-error':''} ${q.status==='done'?'is-done':''}">${q.status==='done'?ui`<button type="button" class="summary-queue-close" data-action="dismiss-summary" aria-label="关闭生成完成提示" title="关闭生成完成提示">×</button>`:''}<div class="summary-queue-head"><div><strong>${esc(scope)} · ${{both:tr('简介＋标签'),tags:tr('仅标签'),summary:tr('仅简介')}[q.target||'summary']} · ${q.mode==='all'?tr('全部生成 / 重新生成'):tr('补充未生成')}</strong><span class="badge ${q.status==='error'?'orange':'green'}">${{running:tr('正在生成'),pausing:tr('准备暂停'),paused:tr('已暂停'),error:tr('等待继续'),done:tr('本轮完成')}[q.status]||''}</span></div><div class="buttons">${q.status==='running'||q.status==='pausing'?`<button class="button small" data-action="pause-summary" ${q.status==='pausing'?'disabled':''}>${q.status==='pausing'?tr('保存本批后暂停…'):tr('暂停')}</button>`:''}${['paused','error'].includes(q.status)?ui`<button class="button primary small" data-action="resume-summary" ${state.busy||state.agentBusy?'disabled':''}>继续生成</button><button class="button plain small" data-action="finish-summary" ${state.busy||state.agentBusy?'disabled':''}>结束本轮</button>`:''}</div></div><div class="summary-queue-counts">已处理 <b>${fmt(q.current)}</b> / ${fmt(q.total)} · 本轮生成 <b>${fmt(q.updated)}</b> · 跳过 ${fmt(q.skipped)}</div>${pending?`<div class="progress"><div data-queue-progress></div></div>`:''}<p>${esc(msg(q.error||q.message))}${q.status==='running'?tr(' 分批生成，完整结果保存后继续。关闭最后一个页面会停止生成并保留进度。'):''}</p></div>`;
}
function paintSummaryQueue(){
  const panel=$('#summary-queue'),q=state.summaryQueue||{};if(panel){const signature=JSON.stringify([q,favoriteView(),state.busy,state.agentBusy]);if(panel.dataset.signature!==signature){panel.innerHTML=summaryQueuePanel();panel.dataset.signature=signature;const progress=$('[data-queue-progress]',panel);if(progress)progress.style.width=(q.total?Math.min(100,q.current/q.total*100):0)+'%';}}
  const button=$('[data-action=ai-all]');if(button)button.disabled=state.busy||state.agentBusy||['running','pausing','paused','error'].includes(state.summaryQueue?.status);
}
function viewSwitch(){return ui`<div class="view-switch" role="group" aria-label="展示方式"><button data-view="list" aria-pressed="${state.view==='list'}" title="列表展示">☷ 列表</button><button data-view="grid" aria-pressed="${state.view==='grid'}" title="大图展示">⊞ 大图</button></div>`;}
function selectionBar(){return ui`<div id="selection-slot" class="selection-slot ${state.selected.size?'':'hidden'}"><div id="selection-bar" class="selectionbar"><span>已选 <b>${fmt(state.selected.size)}</b> 个包 · 跨页和筛选保留</span><button class="button plain small" data-action="clear-selection">清空</button><div class="buttons"><button class="button small" data-action="favorite-selection">☆ 收藏</button><button class="button small" data-action="ai-selection">✧ 生成简介</button><button class="button small" data-action="organize">按分类归档</button><button class="button small" data-action="pin-selection">固定基础包</button><button class="button small" data-action="unpin-selection">解除固定</button><button class="button primary small" data-page="profiles">加入启动方案 →</button></div></div></div>`;}
function updateSelection(){persist();const b=$('#selection-slot');if(b)b.outerHTML=selectionBar();selectionDock.bind();$$('.package-row').forEach(e=>{const on=state.selected.has(e.dataset.key);e.classList.toggle('selected',on);$('input[type=checkbox]',e).checked=on;});const all=$('#select-page');if(all)all.checked=state.items.length>0&&state.items.every(x=>state.selected.has(x.key));}

async function library({id,signal}={}){
  const q={...state.query},r=await api('packages?'+new URLSearchParams({...q,limit:LIBRARY_PAGE_SIZE,with_counts:'1'}),undefined,{signal});
  if(id!==state.renderId)return '';state.items=r.items;state.total=r.total;state.query.page=q.page=r.page;state.itemsQuery=querySignature();
  const s=r.summary,facets=r.facets,favorites=favoriteView(q),filtered=!!(q.q.trim()||q.category||q.state);
  return heading(favorites?tr('收藏夹'):tr('资源库'),favorites?tr`收藏目录：AddonPackages/${esc(state.stats.favorite_folder||'收藏')} · 停用包保留相同分类路径`:tr('把喜欢的资源留在手边，每次只加载需要的内容。'),ui`<button class="button" data-action="deep-scan">深度扫描</button><button class="button" data-action="organize-all" data-archive-scope="${favorites?'favorites':'all'}" title="按分类归档${favorites?tr('全部收藏'):tr('全部资源')}，包含其他页和停用包，不受搜索筛选影响">一键归档</button><button class="button ai-generate" data-action="ai-all" title="选择全部重新生成或仅补充未生成，不受搜索筛选影响">✧ 一键 AI 简介</button><button class="button primary" data-action="import">＋ 添加资源</button>`)+
  ui`<section id="summary-queue" aria-label="AI 简介生成进度">${summaryQueuePanel()}</section>`+
  `<div id="library-stats" class="stats">${stat(filtered?tr('筛选结果'):favorites?tr('收藏资源'):tr('全部资源'),fmt(s.packages),tr`${fmt(s.files)} 个 VAR 文件`,'⊞',true)}${stat(tr('其中启用'),fmt(s.active),tr('当前结果中可被 VaM 扫描到的包'),'▷')}${stat(tr('同名多余副本'),fmt(s.duplicates),tr('当前结果中需校验的额外副本'),'▱')}${stat(tr('资源占用'),bytes(s.bytes),tr`${fmt(s.errors)} 个包需要检查`,'◴')}</div>`+
  ui`<section class="filters"><div class="filter-row"><label class="search"><span>⌕</span><input id="search" placeholder="搜索包名、作者、简介或文件夹…" value="${esc(q.q)}" aria-label="搜索资源"></label><span class="spacer"></span><select id="state-filter" aria-label="筛选状态">${[['',tr('全部状态')],['active',tr('已启用')],['disabled',tr('已停用')],['pinned',tr('固定基础包')],...(!favorites?[['favorite',tr('我的收藏')]]:[]),['unlabeled',tr('未生成 AI 简介')],['errors',tr('读取异常')],['warnings',tr('有感叹号提示')]].map(([v,t])=>`<option value="${v}" ${q.state===v?'selected':''}>${t}</option>`).join('')}</select><select id="sort-filter" aria-label="排序">${[['name',tr('名称排序')],['new',tr('最近修改')],['size',tr('体积从大到小')]].map(([v,t])=>`<option value="${v}" ${q.sort===v?'selected':''}>${t}</option>`).join('')}</select>${viewSwitch()}</div>
  <div id="library-categories" class="chips" title="分类可交叉，同一个包可属于多类；全部数量按包去重统计"><button class="chip ${!q.category?'active':''}" data-category="">全部 <small>${fmt(facets.total)}</small></button>${(state.stats.categories_all||[]).map(c=>`<button class="chip ${q.category===c?'active':''}" data-category="${esc(c)}">${esc(tr(c))}<small>${fmt(facets.categories[c]||0)}</small></button>`).join('')}</div></section>`+
  selectionBar()+ui`<div id="library-results"><div class="list-toolbar"><label><input type="checkbox" id="select-page" ${r.items.length&&r.items.every(x=>state.selected.has(x.key))?'checked':''}> 选择本页 <span>· ${fmt(r.total)} 个结果</span></label><span>${r.total>r.items.length?ui`<button class="linklike" data-action="select-results">选择全部 ${fmt(r.total)} 个结果</button>`:tr("内容分类优先 · 同名多版本分别保留")}</span></div>`+
  (r.items.length?`<div class="package-list ${state.view==='grid'?'grid-view':''}">${r.items.map(p=>ui`<article class="package-row ${state.selected.has(p.key)?'selected':''}" data-key="${esc(p.key)}" tabindex="0" role="group" aria-label="${esc(p.name)} 资源"><input type="checkbox" data-select="${esc(p.key)}" aria-label="选择 ${esc(p.name)}" ${state.selected.has(p.key)?'checked':''}>${cover(p)}${state.view==='grid'?'':categoryBadge(p)}<div class="package-info"><div class="package-name"><button data-detail="${esc(p.key)}">${esc(p.name)}</button><span class="ver">v${esc(p.version||'?')}</span>${warningBadge(p)}</div>${state.view==='grid'?categoryBadge(p):''}${contentTags.html(p)}${summaries.html(p,'library')}<div class="package-meta"><span>${esc(p.author)}</span><span>·</span><span class="${p.source==='ai'?'ai':''}">${{ai:tr('✧ AI 简介'),manual:tr('手动整理'),rules:tr('本地识别')}[p.source]||tr('本地识别')}</span>${p.copies>1?ui`<span>· ${p.copies} 份副本</span>`:''}</div></div><div class="package-side"><span class="badge ${p.error?'red':p.active?'green':''}">${p.error?tr('读取异常'):p.active?tr('已启用'):tr('已停用')}</span><span class="package-size">${bytes(p.size)}</span></div>${favoriteButton(p)}<button class="button plain icon" data-detail="${esc(p.key)}" aria-label="查看详情">›</button></article>`).join('')}</div>`:
  (favorites&&!filtered?empty(tr('还没有收藏资源'),tr('在资源库点击星标，喜欢的资源就会显示在这里。')):empty(state.stats.packages?tr('没有匹配的资源'):tr('开始整理你的 VaM 资源'),state.stats.packages?tr('换个关键词或筛选条件试试。'):ui('点击更新索引，读取 AddonPackages 中的所有 VAR。<br>扫描只建立目录，不会移动你的包。'),state.stats.packages?'':ui('<button class="button primary" data-action="scan">建立索引</button>'))))+pages(q.page,r.total,LIBRARY_PAGE_SIZE,'library-page')+'</div>';
}

function installResult(log){
  if(!log)return '';
  const items=log.items||[],installed=log.installed??items.filter(x=>x.status==='installed').length,
    failed=log.failed??items.filter(x=>x.status==='error').length,notes=items.filter(x=>x.error||x.warnings?.length);
  return ui`<section class="panel download-result"><div class="panel-head"><h2>上次补包结果</h2><span class="muted">${esc(log.created)}</span></div><div class="result-counts"><span>已保存 <b>${fmt(installed)}</b> 个包</span><span>失败 <b>${fmt(failed)}</b> 个包</span>${log.resolved!==undefined?ui`<span>解决 <b>${fmt(log.resolved)}</b> 项依赖</span><span>新发现 <b>${fmt(log.new_missing)}</b> 项依赖</span>`:''}</div><p class="spaced">找到的完整 VAR 已保存；发布信息不一致会显示感叹号，可用性需在 VaM 中确认。读取异常的包继续计入缺失，但不会重复下载。${failed?tr('下载失败项可以重试。'):''}新包也可能引入其他依赖，因此缺失项减少数量可能少于保存数量。</p>${notes.length?ui`<details class="spaced"><summary>查看 ${notes.length} 项失败原因或校验说明</summary><div class="download-notes">${notes.map(x=>`<div class="dependency"><div><strong class="mono">${esc(x.filename)}</strong><p>${esc(msg(x.error)||(x.warnings||[]).map(msg).join('; '))}</p></div><span class="badge ${x.error?'red':'orange'}">${x.error?tr('失败'):tr('! 已保留')}</span></div>`).join('')}</div></details>`:''}</section>`;
}

async function dependenciesPage(){
  const [missing,sources,repairs]=await Promise.all([api('missing?'+new URLSearchParams({page:state.depPage,q:state.depQ})),api('sources'),api('package-repairs')]);state.sources=sources;state.depPage=missing.page;
  const valid=sources.ready||[];
  return heading(tr('依赖检查'),tr('自动核对精确版本与最新版本引用，补齐后立即刷新缺失清单。'),ui`<button class="button" data-action="source-settings">下载来源</button><button class="button" data-action="deep-scan">深度扫描引用</button><button class="button primary" data-action="find-deps">⌕ 查找下载来源</button>`)+
  `<div class="stats">${stat(tr('缺失依赖'),fmt(sources.global_missing_count??missing.total),tr('停用库中已有的包也视为已拥有'),'⛓',true)}${stat(tr('找到下载'),fmt(valid.length),tr('官方 Hub / 作者 GitHub / 添加的可信站点'),'↓')}${stat(tr('仍未解决'),fmt((sources.unresolved||[]).length),tr('含暂无来源和已保存但读取异常的包'),'⌕')}${stat(tr('待下载体积'),bytes(valid.reduce((a,b)=>a+b.bytes,0)),tr('发布页估算，实际下载大小可能不同'),'◴')}</div>`+
  ui`<section class="panel"><div class="panel-head"><div><h2>包异常修复</h2><p>文件名 ${fmt(repairs.names)} 项 · JSON 格式 ${fmt(repairs.json)} 项 · 其他提示 ${fmt(repairs.skipped)} 项</p></div><div class="buttons">${repairs.preview_ready?ui('<button class="button" data-action="package-repair-last">查看上次建议</button>'):''}<button class="button" data-action="package-repair-analyze" data-refresh="${repairs.preview_ready?'true':'false'}">${repairs.preview_ready?tr('重新 AI 分析'):repairs.analyzed?tr`继续分析（已完成 ${repairs.analyzed}）`:tr('✧ 分析与修复')}</button></div></div><p class="spaced">使用简介生成模型，按文件名、meta.json 语法及其他问题分组。先查看建议与修改差异，再选择采用；保留启停位置，支持从操作记录恢复。坏 ZIP、缺少元数据、发布信息不符等无法用 JSON 语法修复的问题会说明原因。</p></section>`+  installResult(sources.last_install)+
  (sources.errors?.length?ui`<div class="notice warning">${sources.errors.length} 次来源查询失败。可重试查找；网络失败不代表资源已下架。<br>${sources.errors.slice(0,2).map(x=>esc(msg(x))).join('<br>')}</div>`:'')+
  (sources.created?ui`<section class="panel"><div class="panel-head"><div><h2>可补充的依赖</h2><p>来源查询：${esc(sources.created)} · 清单已按当前索引核对 · ${sources.scope?.length?tr("仅本次导入的资源及依赖"):tr("整个资源库")}</p></div><button class="button primary" data-action="download-deps" ${!valid.length?'disabled':''}>↓ 下载 ${fmt(valid.length)} 个包</button></div>${valid.length?ui`<div class="table-wrap"><table><thead><tr><th>资源文件</th><th>来源</th><th>大小</th></tr></thead><tbody>${valid.slice(0,80).map(x=>`<tr><td class="mono">${esc(x.filename)}</td><td><span class="badge green">${esc(x.source)}</span></td><td>${bytes(x.bytes)}</td></tr>`).join('')}</tbody></table></div>${valid.length>80?ui`<p class="spaced">此处预览前 80 个；下载按钮处理本次查询的全部 ${valid.length} 个包。</p>`:''}`:ui('<p>尚未找到可直接下载的匹配版本。可以添加可信下载来源，或在下方打开官方 Hub 查询。</p>')}</section>`:'')+
  ui`<section class="panel" id="missing-panel"><div class="panel-head"><h2>缺失清单</h2><label class="search"><span>⌕</span><input id="dependency-search" placeholder="搜索依赖或引用它的包" value="${esc(state.depQ)}" aria-label="搜索缺失依赖"></label></div><p>快速扫描读取包内声明，深度扫描还检查场景和预设的文本引用。下载完成后可再次查找，以补充新引入的依赖。</p>`+
  (missing.items.length?ui`<div class="table-wrap spaced"><table><thead><tr><th>依赖包名</th><th>被引用</th><th>查找</th></tr></thead><tbody>${missing.items.map(d=>ui`<tr><td class="mono">${esc(d.ref)}<div class="sub">${d.users.slice(0,2).map(esc).join('<br>')}</div>${(sources.unresolved||[]).find(x=>x.ref===d.ref)?.reason?.includes('已下载')?ui('<div class="sub">! 已下载保留，读取异常；在资源库查看详情。</div>'):''}</td><td>${fmt(d.count)} 个包</td><td><a target="_blank" rel="noopener noreferrer" href="https://hub.virtamate.com/search/?q=${encodeURIComponent(d.ref)}">官方 Hub ↗</a></td></tr>`).join('')}</tbody></table></div>`:empty(tr('没有发现缺失'),tr('当前索引中的依赖已齐全，或没有匹配的筛选结果。')))+pages(missing.page,missing.total,80,'dep-page')+'</section>';
}

async function duplicatePage(){
  const [a,quarantinePanel]=await Promise.all([api('duplicates'),quarantine.panel().catch(error=>{
    if(error.name==='AbortError')throw error;
    return ui`<section class="panel" id="quarantine-panel"><h2>隔离区</h2><p class="notice warning spaced">${esc(error.message)}</p><button class="button spaced" data-quarantine-action="refresh">刷新隔离区</button></section>`;
  })]);
  return heading(tr('重复清理'),tr('相同名称不一定相同内容；清理前先做 SHA-256 校验。'),ui`<button class="button" data-quarantine-action="jump">查看隔离区</button><button class="button primary" data-action="audit">${a.created?tr('↻ 重新校验'):tr('开始内容校验')}</button>`)+
  `<div class="stats">${stat(tr('同名候选副本'),fmt(state.stats.duplicates),tr('来自当前资源索引'),'▱',true)}${stat(tr('完全相同副本'),fmt(a.extras),tr('按完整文件内容校验'),'✓')}${stat(tr('副本占用'),bytes(a.bytes),tr('永久清理前可恢复'),'◴')}${stat(tr('内容冲突'),fmt(a.conflicts.length),tr('全部保留，不自动清理'),'!')}</div>`+
  (!a.created?`<section class="panel">${empty(tr('先确认哪些文件完全相同'),ui('首次校验需要读取候选包的完整内容，可能需要几分钟。<br>同一资源的不同版本会继续保留。'),ui('<button class="button primary" data-action="audit">校验重复候选</button>'))}</section>`:
  ui`<section class="panel"><div class="panel-head"><div><h2>校验结果</h2>${a.stale?ui('<div class="notice warning">文件库已变化，以下为上次结果，请重新校验后再清理。</div>'):''}<p>${esc(a.created)} · 默认保留已启用、路径更简洁的一份</p></div><button class="button primary" data-action="dedup" ${!a.stale&&a.groups.some(g=>!g.conflict)?'':'disabled'}>预览清理</button></div><div class="notice">重复副本将移入本工具的隔离区，可从「操作记录」恢复。移到隔离区不会立即释放磁盘空间；确认不需要后，可在下方隔离区预览并永久清理。</div><div class="table-wrap"><table><thead><tr><th>包名 / 保留位置</th><th>多余副本</th><th>占用</th><th>处理</th></tr></thead><tbody>${a.groups.slice(0,250).map(g=>`<tr><td class="mono">${esc(g.pkgid)}<div class="sub">${esc(g.keep.rel)}</div></td><td>${g.extras.length}</td><td>${bytes(g.bytes)}</td><td><span class="badge ${g.conflict?'orange':'green'}">${g.conflict?tr('冲突保留'):tr('可清理')}</span></td></tr>`).join('')}</tbody></table></div>${a.groups.length>250?ui('<p class="spaced">页面预览前 250 组，操作计划涵盖全部可清理项。</p>'):''}</section>`)+
  (a.conflicts.length?ui`<section class="panel"><h2>同名内容冲突</h2><p class="spaced">这些包可能经过不同修改。需要判断来源和内容后才能选择，工具不会替你删除。</p>${a.conflicts.map(g=>ui`<div class="operation"><div><h3>${esc(g.pkgid)}</h3><p>${g.variants.length} 种不同内容 · ${g.variants.flat().length} 个文件</p></div><button class="button small" data-detail="${esc(g.pkgid.toLowerCase())}">查看位置</button></div>`).join('')}</section>`:'')+quarantinePanel;
}

async function profilesPage(){
  const profiles=await api('profiles');
  state.selectionPage=Math.min(state.selectionPage,Math.max(1,Math.ceil(state.selected.size/60)));
  const visible=[...state.selected].slice((state.selectionPage-1)*60,state.selectionPage*60);
  return heading(tr('启动方案'),tr('选择主包，自动带上依赖。未选包暂存到停用库，需要时再切回来。'),ui`<button class="button primary" data-action="source-profile">＋ 从收藏 / 文件夹创建</button><button class="button" data-action="stage-all">停用普通包并整理</button><button class="button" data-action="restore-all">恢复全部启用</button>`)+
  ui`<div class="notice">按需启用通过移动 VAR 实现，不删除资源。请在 VaM 退出后切换。${fmt(state.stats.pinned||0)} 个固定基础包及依赖常驻启用；必要会话插件可在详情中固定。<br>停用库：<span class="break">${esc(state.stats.disabled_folder||'')}</span></div><div class="split"><section class="panel"><div class="panel-head"><h2>本次选择 <span class="muted">${state.selected.size}</span></h2><button class="button small" data-page="library">去资源库选择</button></div>`+
  (state.selected.size?ui`<div class="selected-list">${visible.map(k=>ui`<div class="selected-item"><span>${esc(k)}</span><button class="button plain icon" data-remove-selection="${esc(k)}" aria-label="移出选择">×</button></div>`).join('')}</div>${pages(state.selectionPage,state.selected.size,60,'selection-page')}<label class="form-row"><span>方案名称</span><input id="profile-name" value="${esc(state.profileName)}" placeholder="例如：日常舞蹈 / 人物欣赏"></label><div class="buttons"><button class="button" data-action="save-profile">保存方案</button><button class="button primary" data-action="activate">预览并启用</button></div>`:empty(tr('准备一个轻巧的启动方案'),tr('到资源库勾选喜欢的主包，再回来保存方案。')))+
  ui('</section><section class="panel"><div class="panel-head"><h2>已保存的方案</h2></div>')+ (profiles.length?profiles.map(profileCard).join(''):empty(tr('还没有保存的方案'),tr('为常用的资源组合取个名字，下次直接切换。')))+'</section></div>';
}

// Localize the app-owned default name only; renamed and custom profiles keep their text.
function profileDisplayName(p){return p.is_default&&p.name==='基础收藏'?tr('基础收藏'):p.name;}
function profileCard(p){
  const dynamic=['favorites','folder'].includes(p.source?.type);
  const source=p.source?.type==='favorites'?tr('动态跟随全部收藏'):p.source?.type==='folder'?tr('动态跟随：')+esc(p.source.folder):tr('固定主包选择');
  return ui`<div class="profile ${p.is_default?'is-default':''}"><h3>${esc(profileDisplayName(p))} ${p.is_default?ui('<span class="badge green">默认方案</span>'):''} ${p.is_active?ui('<span class="badge green">当前启用</span>'):''} ${dynamic?ui('<span class="badge green">自动更新</span>'):''}</h3><p>${p.keys.length} 个主包 · 含依赖 ${fmt(p.included_count)} 个包</p><p class="profile-source-note">${source}${p.missing_count?ui` · <span class="badge orange">缺失或异常 ${fmt(p.missing_count)} 项</span>`:p.keys.length?tr(' · 依赖齐全'):''}</p><p class="profile-source-note">同名副本：${p.duplicate_mode==='all'?tr('全部保留'):tr('指定副本')}${p.unresolved_copies?ui` · <span class="badge orange">${p.unresolved_copies} 组待选择</span>`:''}</p>${p.is_default?ui('<p class="profile-sync-note">启用后，新增收藏自动启用并带上可用依赖；取消收藏后，仍被收藏引用的包作为依赖保留启用。切换其他方案后停止自动同步。</p>'):p.source?.type==='favorites'?ui('<p class="profile-sync-note">收藏名单自动更新，下次启用时加入可用依赖。</p>'):''}${!p.keys.length&&dynamic?ui('<p>当前来源为空；新增资源后会自动加入此方案。</p>'):''}${p.missing_count?ui`<details class="profile-missing"><summary>查看缺失示例</summary><p class="mono">${p.missing_preview.map(esc).join('<br>')}</p></details>`:''}<div class="buttons"><button class="button primary small" data-profile="${esc(p.id)}" data-action="use-profile" ${!p.keys.length?'disabled':''}>预览启用</button><button class="button small" data-profile="${esc(p.id)}" data-action="load-profile">${dynamic?tr('复制为固定选择'):tr('载入选择')}</button>${p.is_default?'':ui`<button class="button plain small" data-profile="${esc(p.id)}" data-action="delete-profile">删除方案</button>`}</div></div>`;
}

async function historyPage(){
  const [rows,downloads]=await Promise.all([api('history'),api('download-history')]);
  return heading(tr('操作记录'),tr('查看文件变动与恢复状态；永久清理的隔离文件无法恢复。'))+
  ui`<section class="panel"><h2>文件操作</h2><p class="spaced">建议先恢复最新的一项，再恢复较早的操作。源位置已有文件时会停止恢复，避免覆盖。</p>`+
  (rows.length?rows.map(r=>ui`<div class="operation"><div><h3>${esc(kinds[r.kind]||r.kind)} <span class="badge ${r.status==='undone'?'':'orange'}">${{done:tr('已完成'),undone:tr('已恢复'),interrupted:tr('待恢复'),running:tr('可能中断'),undoing:tr('恢复中断'),purging:tr('清理未完成'),partial:tr('部分清理')}[r.status]||esc(r.status)}</span></h3><p>${esc(r.created)} · 计划 ${fmt(r.count)} 个文件${r.favorite_count?tr` · ${fmt(r.favorite_count)} 个收藏状态`:''} · ${bytes(r.bytes)}${r.kind==='purge_quarantine'?(r.status==='purging'?tr(' · 清理中断，剩余文件以隔离区为准'):tr` · 已清理 ${fmt(r.deleted_count)} 个 / ${bytes(r.deleted_bytes)}`):r.irreversible?tr(' · 隔离文件已进入永久清理'):''}</p></div><button class="button small" data-undo="${esc(r.id)}" ${r.status==='undone'||r.irreversible?'disabled':''}>${r.irreversible?tr('无法恢复'):tr('恢复此操作')}</button></div>`).join(''):empty(tr('还没有文件操作'),tr('扫描、浏览和 AI 简介不会移动资源文件。')))+'</section>'+
  ui`<section class="panel"><h2>补包记录</h2>${downloads.length?downloads.map(d=>`<div class="detail-section"><h3>${esc(d.created)}</h3>${d.items.map(x=>`<div class="dependency"><span>${esc(x.filename)}${x.error||x.warnings?.length?`<small class="break"> · ${esc(msg(x.error)||(x.warnings||[]).map(msg).join('; '))}</small>`:''}</span><span class="badge ${x.status==='error'?'red':x.warnings?.length?'orange':'green'}">${x.warnings?.length?tr('! 已保留'):{installed:tr('已保存'),skipped:tr('已存在'),error:tr('失败')}[x.status]}</span></div>`).join('')}</div>`).join(''):empty(tr('还没有下载记录'),tr('补齐依赖后，可以在这里查看成功与失败的项目。'))}</section>`;
}

function apiUsagePanel(){
  const rows=state.aiUsage||[];
  if(!rows.length)return ui('<p class="spaced">更新后首次 API 调用完成时开始记录，Codex CLI 用量不计入此处。</p>');
  return rows.map(r=>{
    const observed=r.cache_observed_tokens||0,hit=r.prompt_cache_hit_tokens||0;
    const rate=observed?`${(hit/observed*100).toFixed(1)}%`:tr('服务未报告');
    const latest=r.last||{},lastRate=latest.cache_observed_tokens?`${(latest.prompt_cache_hit_tokens/latest.cache_observed_tokens*100).toFixed(1)}%`:tr('服务未报告');
    return ui`<div class="notice spaced break"><strong>${esc(r.model)} · ${({chat:tr('对话助手'),summary:tr('仅简介'),tags:tr('仅标签'),both:tr('简介＋标签'),preset:tr('预设草稿'),compaction:tr('上下文摘要'),generation:tr('其他生成')}[r.purpose]||r.purpose)} · ${esc(r.endpoint||r.provider)}</strong><p>请求 ${fmt(r.requests)} 次 · 输入 ${fmt(r.prompt_tokens)} tokens · 输出 ${fmt(r.completion_tokens)} tokens</p><p>累计缓存命中 ${rate} · 已命中 ${fmt(hit)} / 已统计 ${fmt(observed)} 输入 tokens · 最近请求 ${lastRate}</p></div>`;
  }).join('');
}
function paintAPIUsage(){
  const panel=$('#ai-usage');if(!panel)return;
  const html=apiUsagePanel();if(panel.innerHTML!==html)panel.innerHTML=html;
}
async function settingsPage(){
  const c=await api('settings');
  const theme=VaMPMTheme.current();
  return heading(tr('AI 与设置'),tr('选择喜欢的配色，连接你的 AI 服务。'))+ui`<section class="panel settings-panel" id="language-settings"><h2>语言 / Language</h2><label class="form-row"><span>语言 / Language</span><select id="interface-language" aria-label="Language"><option value="zh-CN" ${VaMPMI18n.locale==='zh-CN'?'selected':''}>简体中文</option><option value="en" ${VaMPMI18n.locale==='en'?'selected':''}>English</option></select></label><p>切换后自动保存，并刷新已打开的管家页面；请先保存编辑内容。</p><p>不修改已有的简介、标签和对话内容。</p><p>${tr('新生成的简介、标签和助手回复使用所选语言；文件与文件夹名称保持不变。')}</p></section>`+directorySettings()+
  ui`<section class="panel settings-panel appearance-panel"><div class="panel-head"><div><h2>界面外观</h2><p class="spaced">即点即换，重新打开包管家仍会保留。</p></div><span class="badge green" id="appearance-status" role="status">已自动保存</span></div>
  <div class="appearance-label">主题配色</div><div class="theme-palettes" role="group" aria-label="主题配色">${VaMPMTheme.palettes.map(([v,label])=>`<button class="theme-option" data-palette-choice="${v}" aria-pressed="${theme.palette===v}"><span class="theme-swatch swatch-${v}" aria-hidden="true"><i></i><i></i><i></i></span><span>${label}</span><span class="theme-check" aria-hidden="true">✓</span></button>`).join('')}</div>
  <div class="appearance-label">明暗模式</div><div class="theme-modes" role="group" aria-label="明暗模式">${VaMPMTheme.modes.map(([v,label])=>`<button data-mode-choice="${v}" aria-pressed="${theme.mode===v}"><span aria-hidden="true">${{light:'☀',dark:'☾',system:'◐'}[v]}</span>${label}</button>`).join('')}</div><p class="spaced">跟随系统会自动切换明暗；右上角可快速切换。资源封面的原始颜色保持不变。</p></section>`+
  ui`<section class="panel settings-panel"><div class="panel-head"><h2>AI 服务</h2><span class="badge green">按需调用</span></div><form id="settings-form" data-has-key="${c.has_key}" data-saved-base="${esc(c.base_url)}" data-saved-codex="${esc(c.codex_path)}" data-api-profile="${esc(c.api_profile)}" data-api-profiles="${esc(JSON.stringify(c.api_profiles||{}))}" data-capabilities="${esc(JSON.stringify({manual:c.api_capability_overrides||{},known:c.api_model_capabilities||{}}))}"><label class="form-row"><span>使用哪一种服务</span><select name="provider" id="ai-provider">${[['codex',tr('Codex · 使用已登录的订阅账号')],['deepseek','DeepSeek API'],['compatible',tr('其他 OpenAI 兼容 API')]].map(([v,t])=>`<option value="${v}" ${c.provider===v?'selected':''}>${t}</option>`).join('')}</select></label><div id="codex-settings" class="${c.provider==='codex'?'':'hidden'}"><div class="notice">通过官方 Codex CLI 调用，沿用 CLI 当前账号与可用额度。ChatGPT 登录使用订阅额度；CLI 如果改用 API Key 登录则按 API 计费。模型是否可用取决于你的账号。</div>${ModelPicker.field('codex_model',tr('对话模型'),c.codex_model,tr('用于多轮对话、包管理和预设提示词生成，默认 gpt-6.1-sol。'))}${ModelPicker.field('codex_summary_model',tr('简介生成模型'),c.codex_summary_model,tr('批量简介与导入分析单独使用，默认 gpt-6-luna，节省额度。'))}<div class="buttons"><button type="button" class="button small" data-refresh-models="codex">刷新 Codex 模型</button></div><p class="spaced" data-model-status="codex" role="status"></p><label class="form-row"><span>Codex 程序路径（留空使用内置版本）</span><input name="codex_path" value="${esc(c.codex_path)}" placeholder="C:\…\codex.exe"></label><div class="buttons"><button type="button" class="button" data-action="codex-status">检查登录状态</button><button type="button" class="button" data-action="codex-login">登录 Codex</button></div><div id="codex-result" class="notice hidden"></div></div><div id="api-settings" class="${c.provider==='codex'?'hidden':''}"><p class="notice" data-api-service-note></p><label class="form-row"><span>API 基础地址</span><input name="base_url" value="${esc(c.base_url)}" placeholder="https://api.deepseek.com"></label>${ModelPicker.field('api_model',tr('对话模型'),c.api_model,tr('用于多轮对话、包管理和预设提示词生成。'),c.api_profile!=='deepseek')}${ModelPicker.field('api_summary_model',tr('简介生成模型'),c.api_summary_model,tr('批量简介与导入分析单独使用；图片能力按此模型判断。'),c.api_profile!=='deepseek')}<details class="spaced"><summary>上下文容量设置</summary><label class="form-row"><span>API 上下文容量（tokens）</span><input name="api_context_window" type="number" min="0" max="4000000" step="1" value="${c.api_context_window||0}"><small>0 为自动：优先模型目录，官方 DeepSeek 已知型号按官方容量，其余兼容接口保守使用 65,536。仅在接口未声明或容量不符时手动填写总容量（输入＋输出）；会同时用于对话与生成。</small></label></details><label class="form-row"><span>API 密钥</span><input type="password" name="api_key" autocomplete="new-password" placeholder="${c.has_key?tr('已保存，留空保持原密钥'):tr('粘贴 API Key')}"><small>用 Windows 当前用户加密保存；不会显示在页面或写入 Git。</small></label><button type="button" class="button small" data-refresh-models="api">刷新 API 模型</button><p class="spaced" data-model-status="api" role="status"></p><p class="spaced">能力优先使用手动设置，其次为接口声明和官方资料补充；来源可在模型下方查看。识图未确认时仅使用文字。</p><label class="checkline spaced"><input type="checkbox" name="clear_key">清除已保存的密钥</label></div><label class="checkline spaced"><input type="checkbox" name="summary_images" ${c.summary_images?'checked':''}>生成简介时参考包内封面</label><div class="notice">简介分析会发送作者说明、名称、有限的场景文字线索，并在图片输入开启时附上每包最多一张预览图。仅在所选模型支持识图且参考封面开关开启时发送图片。没有图片时只概括已知内容。不上传 VAR 文件；对话沿用已保存的简介和查询结果。</div><button class="button primary" type="submit">保存设置</button></form></section>`+
  ui`<section class="panel settings-panel"><h2>API 用量与缓存</h2><p class="spaced">按服务、接口、模型及用途分别累计。命中率按服务实际报告的输入 tokens 加权；未报告缓存数据的请求不计入分母。缓存由服务端自动管理。</p><div id="ai-usage" aria-live="polite">${apiUsagePanel()}</div></section>`+
  ui`<section class="panel settings-panel"><h2>固定基础包</h2><p class="spaced">${fmt(state.stats.pin_roots||0)} 个手动固定包，共保留 ${fmt(state.stats.pinned||0)} 个包及可用依赖，常驻 AddonPackages/基础包。</p><p class="spaced">初次保留整合包原基础目录。切换方案、全部停用、收藏和分类整理都保持它们的位置；多分类仍共用一份文件。可以在资源详情或选中后的工具栏固定、解除固定，操作可恢复。</p><button class="button" data-action="base-packages">管理固定基础包</button></section>`+
  ui`<section class="panel settings-panel"><h2>本地工作空间</h2><p class="spaced">关闭最后一个包管家页面后自动退出后台；刷新、最小化或切到其他页面不会退出。未完成的 AI 简介队列会保留，重新打开后可继续。</p><p class="spaced break">游戏目录：${esc(state.stats.game||'')}</p><div class="buttons spaced"><button class="button" data-action="open-folder">打开工具文件夹</button><button class="button danger" data-action="shutdown">退出包管家</button></div><ol class="guide-list"><li>更新索引：发现新增包并按当前方案同步启停；未应用方案时仅启用基础包。深度扫描还补充场景内部引用。</li><li>按分类归档：普通停用、收藏和普通启用资源分别分类，同时校正启停；先预览，保留恢复记录。</li><li>按需启用：保存主包组合，工具自动加入依赖。</li><li>移走整个工具目录之前，请先恢复全部启用，并恢复需要保留的隔离副本。</li></ol></section>`;
}

function mergeChat(r){
  if(chat.id!==r.id){chat.id=r.id;chat.rows.clear();chat.seq=0;}
  r.messages.forEach(m=>chat.rows.set(m.id,m));chat.seq=r.seq;chat.busy=r.busy;chat.context=r.context||null;
}
function recommendationCards(row){
  return ui`<div class="recommendation-heading">${esc(row.title||tr('为你推荐'))} <span>${(row.items||[]).length} 个包</span></div><div class="recommendation-grid">${(row.items||[]).map(p=>ui`<article class="recommendation-card" data-key="${esc(p.key)}" tabindex="0" role="group" aria-label="${esc(p.name)} 推荐资源"><button class="recommendation-open" data-detail="${esc(p.key)}" aria-label="打开 ${esc(p.name)}">${cover(p)}</button><div class="recommendation-content"><div class="recommendation-name"><strong title="${esc(p.name)}">${esc(p.name)}</strong><small>v${esc(p.version)}</small></div>${categoryBadge(p)}${contentTags.html(p)}${summaries.html(p,row.id)}<span>${esc(p.author)}</span></div><div class="recommendation-footer"><span class="badge ${p.error?'red':p.active?'green':''}">${p.error?tr('读取异常'):p.active?tr('已启用'):tr('已停用')}</span>${warningBadge(p)}<span class="spacer"></span>${favoriteButton(p)}</div></article>`).join('')}</div>`;
}
async function loadFolderChoices(q=''){
  const result=await api('folders?'+new URLSearchParams({q,limit:100}));
  if($('#folder-choices'))$('#folder-choices').innerHTML=result.items.map(x=>ui`<option value="${esc(x.path)}">${fmt(x.count)} 个包</option>`).join('');
}
async function sourceProfile(){
  openModal(ui`<h2>从收藏或文件夹创建启动方案</h2><p>方案会跟随来源更新，每次预览和启用时自动加入递归依赖。保存方案不会切换当前包库。</p><form id="profile-source-form"><label class="form-row"><span>方案来源</span><select id="profile-source" name="source"><option value="favorites">全部收藏</option><option value="folder">指定文件夹（包含子文件夹）</option></select></label><label class="form-row hidden" id="folder-field"><span>AddonPackages 内的文件夹</span><input id="profile-folder" name="folder" list="folder-choices" placeholder="例如：${esc(state.stats.favorite_folder||'收藏')}/场景"><datalist id="folder-choices"></datalist><small>已停用包的相同路径也会计入。输入关键词可查找文件夹。</small></label><label class="form-row"><span>方案名称</span><input name="name" required maxlength="60" value="收藏精选"></label><div class="modal-actions"><button type="button" class="button" data-action="close-modal">取消</button><button type="submit" class="button primary">保存动态方案</button></div></form>`);
  await loadFolderChoices();
}
function renderMarkdown(body,text){
  const html=marked.parse(text||'',{gfm:true,breaks:true});
  body.innerHTML=DOMPurify.sanitize(html,{ALLOWED_TAGS:['p','br','strong','em','del','ul','ol','li','h1','h2','h3','h4','h5','h6','blockquote','pre','code','hr','table','thead','tbody','tr','th','td','a'],ALLOWED_ATTR:['href','title','start']});
  body.classList.add('markdown');
  for(const a of body.querySelectorAll('a')){
    if(!/^https?:\/\//i.test(a.getAttribute('href')||''))a.removeAttribute('href');
    else{a.target='_blank';a.rel='noopener noreferrer';}
  }
}
async function sourceSettings(){
  const current=await api('source-settings');
  openModal(ui`<h2>下载来源</h2><p>默认查询 VaM Hub，并从本地包的作者链接识别 GitHub 仓库、匹配 Releases 中的 VAR。也可添加已确认的作者或正规网站公开下载页。</p><div class="source-rows">${current.sites.map(s=>ui`<div class="operation"><div><h3>${esc(s.name)}</h3><p>${esc(s.prefix)} · <a href="${safeLink(s.url)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a></p></div><button class="button small" data-action="remove-source" data-source="${esc(s.id)}">移除</button></div>`).join('')}</div><form id="source-form" class="spaced"><div class="form-grid"><label class="form-row"><span>来源名称</span><input name="name" required maxlength="80" placeholder="作者官网"></label><label class="form-row"><span>匹配的包</span><input name="prefix" required placeholder="作者.* 或 作者.包名"></label></div><label class="form-row"><span>公开页面、GitHub 仓库或 VAR 直链</span><input name="url" type="url" required placeholder="https://…"><small>支持页面中的 VAR 链接及公开 JSON 目录；登录、付费和网盘提取页面需手动获取。</small></label><label class="form-row"><span>额外下载域名（可选，多个用逗号分隔）</span><input name="download_hosts" placeholder="downloads.example.com"><small>留空仅允许同站及内置官方域名。填写你确认可信的下载 CDN。</small></label><div class="modal-actions"><button type="button" class="button" data-action="close-modal">完成</button><button class="button primary" type="submit">添加来源</button></div></form>`);
}
async function assistantPage(){
  const [conversation]=await Promise.all([api('agent/state?'+new URLSearchParams({after:chat.seq,chat_id:chat.id})),chatPresets.load()]);
  mergeChat(conversation);
  return ui`<section class="chat-shell"><div class="chat-topline"><div class="chat-title"><h1>对话助手</h1><span id="chat-provider">Codex / API</span></div><div class="chat-commands"><span id="chat-status">就绪</span><button class="button small" data-action="chat-new">新对话</button><button class="button small" data-page="settings">连接设置</button></div></div>
    <div id="chat-thread" class="chat-thread" aria-live="polite"></div>
    ${chatPresets.bar()}
    <form id="chat-form" class="chat-composer"><div class="chat-resize-handle" role="separator" tabindex="0" aria-label="调整输入框高度" aria-orientation="horizontal" aria-controls="chat-input" aria-valuemin="48" aria-valuemax="480" aria-valuenow="48" title="拖动上边缘调整输入框高度，也可用上下方向键调整"></div><textarea id="chat-input" rows="2" maxlength="12000" placeholder="例如：把选中的人物包归档，创建一个“人物欣赏”启动方案…" aria-label="发送给助手的消息">${esc(chat.draft)}</textarea><div class="chat-actions"><label class="checkline"><input id="chat-read-only" type="checkbox" ${chat.readOnly?'checked':''}>只读推荐</label><span class="chat-hint">Enter 发送 · Shift + Enter 换行</span><button class="button danger small" type="button" id="chat-stop" data-action="chat-stop">停止</button><button class="button primary" type="submit" id="chat-send">发送 ↑</button></div></form></section>`;
}
function paintChat(){
  const thread=$('#chat-thread');if(!thread)return;
  const atBottom=thread.scrollHeight-thread.scrollTop-thread.clientHeight<100;let cardsChanged=false;
  const rows=[...chat.rows.values()].slice(-120),ids=new Set(rows.map(x=>x.id));
  for(const el of [...thread.children])if(!ids.has(el.dataset.id))el.remove();
  if(!rows.length){thread.innerHTML=ui('<div class="chat-welcome"><div>✧</div><h2>让资源管理变得简单</h2><p>可以继续追问，也可以直接交给我处理。<br>每次操作都会显示结果，文件转移支持恢复。</p></div>');}
  for(const row of rows){
    let node=[...thread.children].find(n=>n.dataset.id===row.id);
    if(node&&node.dataset.seq===String(row.seq))continue;
    if(!node){node=document.createElement('article');node.dataset.id=row.id;thread.append(node);}
    node.dataset.seq=row.seq;node.className='chat-message '+row.role;
    if(row.role==='recommendations'){
      node.innerHTML=recommendationCards(row);cardsChanged=true;
    }else if(row.role==='tool'){
      let details=$('details',node);
      if(!details){node.innerHTML='<details class="chat-tool"><summary></summary><div class="chat-tool-progress"></div><div class="chat-package-links"></div><pre></pre></details>';details=$('details',node);}
      $('summary',details).textContent=({running:'◌ ',done:'✓ ',error:'! '}[row.status]||'')+(toolNames[row.name]||row.name);
      details.dataset.status=row.status;
      $('.chat-tool-progress',details).textContent=msg(row.progress)||'';
      $('pre',details).textContent=JSON.stringify(row.args,null,2)+(row.result?'\n\n'+row.result:'');
      let result;try{result=JSON.parse(row.result||'{}');}catch{}
      const packages=Array.isArray(result?.items)?result.items.filter(x=>x.key&&x.name).slice(0,8):[];
      $('.chat-package-links',details).innerHTML=packages.map(p=>`<button type="button" data-detail="${esc(p.key)}">${esc(p.name)} <small>${esc(tr(p.category))}</small></button>`).join('');
    }else{
      if(!$('.chat-body',node))node.innerHTML='<div class="chat-role"></div><div class="chat-body"></div>';
      $('.chat-role',node).textContent=({user:tr('你'),assistant:tr('包管家'),error:tr('连接提示'),notice:tr('任务提示')}[row.role]||'')+(row.selection_count?tr` · 附带 ${fmt(row.selection_count)} 个已选包`:'');
      const body=$('.chat-body',node);
      if(row.role==='assistant')renderMarkdown(body,row.text);else body.textContent=row.text;
    }
  }
  $('#chat-status').textContent=(chat.busy?tr('正在处理…'):tr('就绪'))+(chat.context?tr` · 上下文约 ${fmt(chat.context.estimated_input_tokens)} tokens`: '');
  $('#chat-stop').classList.toggle('hidden',!chat.busy);$('#chat-send').disabled=chat.busy;
  $('[data-action="chat-new"]').disabled=chat.busy;
  if(cardsChanged){covers.watch();summaries.sync();}
  if(atBottom)thread.scrollTop=thread.scrollHeight;
}
async function refreshChat(){
  if(chat.refreshing||state.page!=='assistant')return;chat.refreshing=true;
  try{const r=await api('agent/state?'+new URLSearchParams({after:chat.seq,chat_id:chat.id}));mergeChat(r);paintChat();if($('#chat-provider'))$('#chat-provider').textContent=(r.provider==='codex'?'Codex':r.provider||tr('沿用 AI 设置'))+(r.model?' · '+r.model:'');}
  finally{chat.refreshing=false;}
}
async function sendChat(){
  const input=$('#chat-input'),message=input.value.trim();if(!message||chat.busy)return;
  chat.readOnly=$('#chat-read-only').checked;
  // The backend resolves the complete selection by reference, without truncation or huge prompts.
  await api('agent/send',{message,read_only:chat.readOnly,selected_keys:[...state.selected]});
  chat.draft='';input.value='';await refreshChat();clearTimeout(pollTimer);pollTimer=setTimeout(poll,0);
}

async function render(resultsOnly=false){
  document.body.classList.toggle('assistant-view',state.page==='assistant');
  renderController?.abort();renderController=new AbortController();
  const id=++state.renderId;nav();
  try{
    const html=await ({home:homePage,library,assistant:assistantPage,dependencies:dependenciesPage,duplicates:duplicatePage,profiles:profilesPage,history:historyPage,settings:settingsPage}[state.page])({id,signal:renderController.signal});
    if(id!==state.renderId)return;
    const scope=state.page==='library'?(favoriteView()?'favorites':'all'):'';
    if(state.page==='home'&&$('.home-grid')){paintHome();}
    else if(resultsOnly&&state.page==='library'&&$('#library-results')&&$('#main').dataset.libraryScope===scope){
      const template=document.createElement('template');template.innerHTML=html;
      for(const selector of ['#library-stats','#library-categories','#library-results'])$(selector).replaceWith($(selector,template.content));
    }else{$('#main').innerHTML=html;$('#main').dataset.libraryScope=scope;}
    covers.watch();paintHome();paintSummaryQueue();VaMPMTheme.paint();pagerDock.bind();selectionDock.bind();modelPicker.bind();summaries.sync();chatComposer.bind();
    if(state.page==='assistant'){paintChat();await refreshChat();}
  }catch(e){if(e.name==='AbortError'||id!==state.renderId)return;$('#main').innerHTML=empty(tr('暂时无法读取'),esc(e.message),ui('<button class="button" data-action="retry">重试</button>'));pagerDock.bind();toast(e.message,true);}
}
async function refresh(){const themeRevision=VaMPMTheme.revision(),s=await api('status');if(s.language?.language&&s.language.language!==VaMPMI18n.locale){sessionStorage.setItem('vampm-language-page',state.page);reloadWorkspace();throw new DOMException('Language changed','AbortError');}adoptWorkspace(s);const layout=s.home_layout||{cards:HOME_DEFAULT};state.homeLayoutChanged=!!state.homeLayout&&JSON.stringify(state.homeLayout.cards)!==JSON.stringify(layout.cards);state.homeLayout=layout;loadLibraryPreferences(s.library_preferences);VaMPMTheme.sync(s.appearance,themeRevision);state.stats=s.stats;state.busy=s.busy;state.revision=s.revision;state.agentBusy=s.agent_busy;state.summaryQueue=s.summary_queue||{};state.aiUsage=s.ai_usage||[];paintAPIUsage();paintSummaryQueue();paintHome();$('#recovery-banner').classList.toggle('hidden',!s.pending);nav();return s;}
async function changePage(page){clearTimeout(searchTimer);state.page=page;await render();window.scrollTo({top:0,behavior:'instant'});}
async function startJob(path,data){const j=await api(path,data);state.busy=true;showJob(j);toast(msg(j.title)+tr('已开始'));clearTimeout(pollTimer);pollTimer=setTimeout(poll,0);return j;}
function showJob(j){const e=$('#job-panel'),summary=!!j.result?.summary_queue||j.title.endsWith(' · AI 简介')||j.title.endsWith(' · AI 简介与标签');e.classList.remove('hidden');if(e.dataset.job!==j.id){e.dataset.job=j.id;e.innerHTML=`<div class="job-title"><b>${esc(msg(j.title))}</b><button class="button plain small" ${summary?'data-action="pause-summary"':`data-cancel-job="${j.id}"`}>${summary?tr('暂停'):tr('取消')}</button></div><p></p><div class="progress"><div></div></div>`;}if(summary){const b=$('button',e);b.disabled=state.summaryQueue?.status==='pausing';b.textContent=b.disabled?tr('准备暂停'):tr('暂停');}$('p',e).textContent=msg(j.message)||tr('准备中…');$('.progress>div',e).style.width=(j.total?Math.min(100,j.current/j.total*100):4)+'%';}
async function poll(){
  if(polling||stopped)return;polling=true;clearTimeout(pollTimer);
  try{
    const oldRevision=state.revision;
    const s=await refresh(),running=s.jobs.find(j=>j.status==='running');if(running)showJob(running);else $('#job-panel').classList.add('hidden');
    let changed=oldRevision!==state.revision||(state.page==='home'&&state.homeLayoutChanged);
    for(const summary of s.jobs.slice().reverse()){
      if(summary.status==='running'||state.seenJobs.has(summary.id))continue;
      const j=await api('job?id='+encodeURIComponent(summary.id));state.seenJobs.add(j.id);changed=true;
      if(j.origin==='preset'){chatPresets.draftResult(j);continue;}
      if(j.origin==='agent')continue;
      if(j.status==='error'||j.status==='cancelled')toast(msg(j.error)||tr('任务未完成'),true);
      else if(j.result?.summary_queue){const q=j.result.summary_queue;toast(q.status==='paused'?tr('AI 简介已暂停，已生成结果已保存'):tr`AI 简介完成：本轮生成 ${q.updated} 个，跳过 ${q.skipped} 个。`);}
      else if(j.result?.package_repair_preview)showPackageRepairPreview(j.result);
      else if(j.result?.kind==='repair_packages'){state.selected.clear();openModal(ui`<h2>包异常修复完成</h2><p>已完成 ${fmt(j.result.repaired)} 项修复。索引已更新，原启停位置保留，可从操作记录按倒序恢复。</p><p class="spaced">JSON 修复只处理元数据语法，其他资源内容保持原样；缺失依赖仍需另行检查。若同一个包还有文件名异常，请再次分析。</p><div class="modal-actions"><button class="button primary" data-action="close-modal">完成</button></div>`);}
      else if(j.result?.name_repair_preview)showNameRepairPreview(j.result);
      else if(j.result?.kind==='repair_names'&&j.result.repaired!==undefined){state.selected.clear();openModal(ui`<h2>文件名修复完成</h2><p>已修复 ${fmt(j.result.repaired)} 个包。资源已重新识别，保持原启停状态，可在操作记录恢复旧名。</p><p class="spaced">如有同名副本，请在启动方案中选择需要的版本内容；缺失依赖和其他场景问题仍按原流程检查。</p><div class="modal-actions"><button class="button primary" data-action="close-modal">完成</button></div>`);}
      else if(j.result?.quarantine_preview){if(j.id===quarantine.pendingPreview){quarantine.pendingPreview=null;quarantine.showPreview(j.result.quarantine_preview);}}
      else if(j.result?.kind==='purge_quarantine')toast(tr`隔离区清理完成：删除 ${fmt(j.result.count)} 个文件，合计 ${bytes(j.result.bytes)}。`);
      else if(j.result?.preview)showPlan(j.result);
      else if(j.title==='导入资源')showImportResult(j.result);
      else if(j.result?.installed!==undefined)toast(tr`补包完成：安装 ${j.result.installed} 个，失败 ${j.result.failed} 个；解决 ${j.result.resolved||0} 项依赖，新发现 ${j.result.new_missing||0} 项。`,j.result.failed>0);
      else if(['favorite','unfavorite'].includes(j.result?.kind))toast(msg(j.title)+(j.result.auto_sync?tr('已完成；已按当前方案同步启停'):tr('已完成；收藏名单已更新')));
      else toast(msg(j.title)+tr('已完成'));
    }
    if(changed&&!['settings','assistant'].includes(state.page))await render(state.page==='library');
    if(changed&&state.detail&&!$('#drawer-backdrop').classList.contains('hidden'))await detail(state.detail.key);
    if(state.page==='assistant')await refreshChat();
  }catch(e){$('#footer-status').textContent=tr('连接已断开 · 双击 EXE 可重新打开');}
  polling=false;if(!stopped)pollTimer=setTimeout(poll,document.hidden?15000:state.busy||state.agentBusy?350:state.page==='assistant'?1500:4000);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){clearTimeout(pollTimer);pollTimer=setTimeout(poll,0);}});
function openModal(html){$('#dialog-content').innerHTML=html;if(!$('#dialog').open)$('#dialog').showModal();}
function closeModal(){$('#dialog').close();}
function showPlan(p){
  state.plan=p;
  const unresolved=p.unresolved_copies?.length||0;
  openModal(ui`<h2>${esc(kinds[p.kind])}</h2>${p.archive_scope?ui`<p class="notice">归档范围：${p.archive_scope==='favorites'?tr('全部收藏'):tr('全部资源')} · ${fmt(p.archive_packages)} 个包，包含停用包，不受搜索筛选或分页影响。</p>`:''}<p>${unresolved?tr('完成副本选择后计算文件转移。'):tr`计划移动 ${fmt(p.count)} 个文件${p.attachment_count?tr`（含 ${p.attachment_count} 个附件）`:''}，涉及 ${bytes(p.bytes)}。`}${p.kind==='activate'?tr`选择 ${p.selected} 个主包，合计启用 ${p.included} 个含依赖的包${unresolved?tr('（暂按各副本依赖汇总）'):tr` · ${fmt(p.included_files)} 份文件`}。`:''}</p>${VaMPMActivation.render(p,{esc,bytes,areas})}${p.warnings.map(w=>`<div class="notice">${esc(msg(w))}</div>`).join('')}${p.kind==='activate'&&!p.included_files&&!unresolved?ui('<div class="notice error">当前方案没有可读取的包，请至少选择一个可用包后再启用。</div>'):''}${p.missing.length?ui`<div class="notice warning">还有 ${p.missing.length} 项依赖缺失或包不可读取，仍可启用已有可用包。缺失内容或对应功能可能无法使用。${unresolved?tr('完成副本选择后会重新计算。'):''}<br>${p.missing.slice(0,12).map(esc).join('<br>')}</div>`:''}<div class="plan-list">${p.preview.length?p.preview.map(m=>`<div class="plan-item">${areas[m.from_area]} / ${esc(m.from_rel)}<span>↓</span>${areas[m.to_area]} / ${esc(m.to_rel)}</div>`).join(''):unresolved?tr('尚未生成转移清单。'):tr('当前位置已经符合方案，无需移动。')}</div>${p.count>80?ui('<p>仅预览前 80 个文件，操作记录会保存完整路径。</p>'):''}<div class="modal-actions"><button class="button" data-action="close-modal">取消</button>${p.kind==='activate'?ui('<button class="button" data-action="update-activation-preview">更新预览</button>'):''}<button class="button primary" data-action="apply-plan" ${unresolved||(p.kind==='activate'?!p.included_files:(!p.count&&!['pin','unpin','stage_all','restore_all'].includes(p.kind)))?'disabled':''}>确认执行</button></div>`);
}
async function detail(key){
  const request=++detailRequest,p=await api('package?key='+encodeURIComponent(key));if(request!==detailRequest)return;
  const scroll=state.detail?.key===p.key?$('#drawer').scrollTop:0;state.detail=p;
  $('#drawer').innerHTML=ui`<div class="drawer-head"><span>PACKAGE DETAILS</span><button class="button plain icon" data-action="close-drawer" aria-label="关闭详情">×</button></div>${cover(p,'detail-cover')}<h2>${esc(p.name)}</h2><p class="author">${esc(p.author)} · v${esc(p.version)} · ${bytes(p.size)}</p><div class="buttons">${categoryBadge(p,true)}<span class="badge">${p.active?tr('已启用'):tr('已停用')}</span>${favoriteButton(p)}</div><div id="cover-area"></div><div class="detail-section">${contentTags.html(p,true)}<p class="detail-summary">${esc(p.summary)}</p>${p.error?`<div class="notice error">${esc(msg(p.error))}</div>`:''}${p.warnings?.length?ui`<div class="notice warning"><strong>! 资源提示 · 文件已保留</strong><ul>${p.warnings.map(w=>`<li>${esc(msg(w))}</li>`).join('')}</ul></div>`:''}</div><div class="buttons"><button class="button primary small" data-action="detail-select">${state.selected.has(p.key)?tr('从选择中移除'):tr('加入选择')}</button><button class="button small" data-action="detail-ai">✧ AI 重新分析</button><button class="button small" data-action="detail-folder">打开位置</button><button class="button small" data-action="detail-pin">${p.pin_root?tr("解除固定"):tr("固定基础包")}</button></div>${p.pinned?ui`<div class="notice spaced"><strong>固定基础包 · 常驻启用</strong><p>${p.pin_root?tr("手动固定；解除后若仍被其他固定包依赖会继续保留。"):ui`作为基础依赖自动保留。来自 <button class="linklike" data-detail="${esc(p.pin_parent||p.key)}">${esc(p.pin_parent||tr("其他固定包"))}</button>；可进入该包详情解除上游固定。`}</p><p>分类和收藏只改变浏览标签，文件留在基础包目录。</p></div>`:""}<details class="detail-section"><summary>编辑分类、简介与标签</summary><form id="label-form" class="spaced"><label class="form-row"><span>主分类（实际存放目录）</span><select name="category">${state.stats.categories_all.map(c=>`<option value="${esc(c)}" ${p.category===c?'selected':''}>${esc(tr(c))}</option>`).join('')}</select></label><label class="form-row"><span>中文简介</span><textarea name="summary" rows="3">${esc(p.summary)}</textarea></label><label class="form-row"><span>内容标签（逗号分隔，最多24个）</span><textarea name="tags" rows="2">${esc((p.tags||[]).join('，'))}</textarea></label><button class="button small" type="submit">保存修改</button></form></details>${p.resource_folders?.length?ui`<div class="notice spaced"><strong>整组资源文件夹</strong><p>${p.resource_folders.map(g=>ui`${esc(g.name)} · 主包 <button class="linklike" data-detail="${esc(g.owner)}">${esc(g.owner)}</button>`).join("<br>")}</p><p>分类与收藏保留组内结构，图片和文档随主包启用；固定基础依赖留在基础包。</p></div>`:""}<div class="detail-section"><h3>资源位置 · ${p.files.length} 份</h3>${p.files.map(f=>ui`<div class="file">${areas[f.area]} / ${esc(f.rel)}<br>${bytes(f.size)} <button class="linklike" data-action="detail-folder" data-file-id="${f.id}">定位文件</button></div>`).join('')}</div><div class="detail-section"><h3>依赖 · ${p.dependencies.length} 项</h3><p>${p.deep===1?tr('已检查包内文本引用'):p.deep===2?tr('深度扫描包含大小超限的跳过项'):tr('当前仅包含元数据声明，可运行深度扫描')}</p>${p.dependencies.length?p.dependencies.map(d=>`<div class="dependency"><span>${esc(d.ref)}${d.resolved&&d.ref!==d.resolved?`<small><br>→ ${esc(d.resolved)}</small>`:''}</span><span class="badge ${d.resolved?'green':'red'}">${d.resolved?d.active?tr('已拥有'):tr('在停用库'):tr('缺失')}</span></div>`).join(''):ui('<p>未声明外部依赖。</p>')}</div><div class="detail-section"><h3>包内内容摘要</h3><ul>${p.samples.slice(0,15).map(f=>`<li class="mono">${esc(f)}</li>`).join('')}</ul></div>${p.url?ui`<div class="detail-section"><a target="_blank" rel="noopener noreferrer" href="${safeLink(p.url)}">访问作者页面 ↗</a><p>${esc(p.license)}</p></div>`:''}`;
  $('#drawer-backdrop').classList.remove('hidden');$('#drawer').scrollTop=scroll;covers.watch();
}
function closeDrawer(){detailRequest++;state.detail=null;$('#drawer-backdrop').classList.add('hidden');$('#drawer').replaceChildren();covers.watch();}
let importOptions=null,importTransfer=null;
async function showImport(){
  importOptions=await api('import-options');
  const target=importOptions.area==='vault'?(state.workspace?.disabled_folder||'.DisabledPackages'):'AddonPackages';
  openModal(ui`<h2>添加资源</h2><p>选择 VAR 文件或整个文件夹，复制资源并保留原文件。</p><div class="notice">${esc(msg(importOptions.reason))}。本次先存入 <strong>${target}/包管家导入/</strong>。${importOptions.on_demand?tr('新包先进入停用库；导入完成后按当前方案同步，所需依赖自动启用，未应用方案时仅启用基础包。'):tr('新包加入启用库。')}</div><label class="form-row"><span>选择 VAR 文件</span><input type="file" id="import-files" accept=".var" multiple></label><label class="form-row"><span>或选择文件夹</span><input type="file" id="import-folder" webkitdirectory multiple></label><p>文件夹递归包含 VAR、JPG、PNG、WEBP、GIF、BMP、TXT、MD、PDF、JSON，保留子目录。压缩合集请先解压；图片和文档保留导入时的子目录；全部停用/恢复时随库转移。</p><div id="import-selection" class="notice">尚未选择文件。</div><label class="checkline"><input type="checkbox" id="import-ai" checked>为新包生成 AI 简介和分类</label><label class="checkline spaced"><input type="checkbox" id="import-find" checked>同时查找缺失依赖的可信下载来源</label><p class="spaced">已有相同内容跳过；同名不同内容保留独立副本。AI 不可用时使用本地分类；下载依赖由你在依赖页面发起。</p><div id="upload-progress" class="notice hidden" aria-live="polite"></div><div class="modal-actions"><button class="button" data-action="close-modal">取消</button><button class="button hidden" data-action="import-cancel">停止上传</button><button class="button primary" data-action="import-start">导入并分析</button></div>`);
}
function importSelection(){
  const folder=$('#import-folder'),files=folder.files.length?[...folder.files]:[...$('#import-files').files];
  return VaMPMImports.selection(files,importOptions);
}
function showImportSelection(){
  const s=importSelection();
  $('#import-selection').textContent=tr`已选择 ${s.packages} 个 VAR、${s.attachments} 个附件，共 ${bytes(s.files.reduce((n,f)=>n+f.size,0))}。${s.ignored?tr`已忽略 ${s.ignored} 个不支持的文件。`:''}${s.files.length>importOptions.max_files?tr('一次最多 10,000 个文件，请分批导入。'):''}`;
}
async function importStart(){
  const {files}=importSelection();if(!files.length)throw Error(tr('请选择 VAR 文件，或含支持文件的文件夹。'));
  if(files.length>importOptions.max_files)throw Error(tr('一次最多导入 10,000 个文件，请分批选择。'));
  const ai=$('#import-ai').checked,find=$('#import-find').checked;
  const progress=$('#upload-progress');progress.classList.remove('hidden');
  $$('button,input',$('#dialog')).forEach(b=>b.disabled=true);
  const cancel=$('[data-action="import-cancel"]');cancel.classList.remove('hidden');cancel.disabled=false;
  const transfer=importTransfer={cancelled:false},ids=[];let completed=0,started=false;
  const total=files.reduce((n,f)=>n+f.size,0);
  try{
    for(let i=0;i<files.length;i++){
      if(transfer.cancelled)break;
      const update=loaded=>{if(!transfer.cancelled)progress.textContent=tr`正在复制 ${i+1}/${files.length}：${files[i].name} · ${bytes(completed+loaded)} / ${bytes(total)}`;};
      update(0);const out=await VaMPMImports.upload(files[i],token,update);ids.push(out.id);completed+=files[i].size;
    }
    if(!transfer.cancelled){await startJob('import',{ids,ai,find});started=true;closeModal();}
    else progress.textContent=tr('已停止上传，正在清理暂存文件…');
  }finally{
    if(!started&&ids.length)await api('discard-import',{ids}).catch(()=>toast(tr('暂存清理未完成，过期文件会在下次启动时清理。'),true));
    if(!started){$$('button,input',$('#dialog')).forEach(b=>b.disabled=false);cancel.classList.add('hidden');if(transfer.cancelled)progress.textContent=tr('已停止，原文件和资源库保持不变。');}
    importTransfer=null;
  }
}
function showImportResult(r){
  openModal(ui`<h2>资源导入完成</h2><p>VAR 成功 ${r.installed} 个，附件复制 ${r.attachments||0} 个，重复跳过 ${r.skipped} 个，失败 ${r.failed} 个。</p><div class="notice">已按当前方案同步启停：所需包及依赖启用，其余保持停用；未应用方案时仅启用固定基础包。整组资源按主分类存放，组内层级保留，实际位置可在详情中查看。原文件未修改。</div>${r.ai_error?ui`<div class="notice warning">AI 暂未生成：${esc(msg(r.ai_error))}<br>已使用本地规则分类，之后可选中包补生成简介。</div>`:''}<div class="plan-list">${r.items.slice(0,200).map(x=>`<div class="plan-item">${esc(x.filename)} · ${esc(msg((x.message||x.status).replace(/ · (停用|启用)库$/, '')))}${x.warnings?.length?`<br>! ${x.warnings.map(x=>esc(msg(x))).join('；')}`:''}</div>`).join('')}</div>${r.items.length>200?ui('<p>仅显示前 200 个文件，完整结果保存在导入记录中。</p>'):''}<div class="notice">新增资源相关缺失依赖：${r.missing} 项。${r.source_error?esc(msg(r.source_error)):tr('可在依赖页面查看和补充。')}</div><div class="modal-actions"><button class="button" data-action="close-modal">完成</button><button class="button primary" data-action="import-to-deps">查看依赖</button></div>`);
}

document.addEventListener('change',e=>{
  if(e.target.matches('[data-package-repair-all]'))$$('[data-package-repair-select]:not(:disabled)').forEach(x=>x.checked=e.target.checked);
  if(e.target.matches('[data-name-repair-all]'))$$('[data-name-repair-select]').forEach(x=>x.checked=e.target.checked);
});
function showPackageRepairPreview(p){
  state.packageRepair=p;
  const rows=type=>p.items.filter(i=>i.type===type).map(i=>ui`<div class="name-repair-row" data-repair-type="${type}" data-repair-id="${i.id}"><input type="checkbox" data-package-repair-select ${i.repairable?'checked':'disabled'} aria-label="选择修复 ${esc(i.rel)}"><div><div class="mono break">${esc(areas[i.area])} / ${esc(i.rel)}</div>${type==='name'?ui`<label class="form-row"><span>新文件名</span><input data-name-repair-filename value="${esc(i.filename)}" ${i.repairable?'':'disabled'} spellcheck="false"></label><span class="badge ${i.confidence==='high'?'green':'orange'}">${{high:tr('依据明确'),medium:tr('需要核对'),low:tr('推测名称')}[i.confidence]}</span>${i.conflict?ui('<span class="badge orange">已有同名包 · 分别保留</span>'):''}`:`<p class="mono break">${esc(msg(i.error))}</p><span class="badge ${i.repairable?'green':'orange'}">${i.repairable?tr('JSON 校验通过'):tr('暂不采用')}</span>`}<p class="muted spaced">${esc(i.reason)}</p>${type==='name'&&i.evidence?ui`<details><summary>查看判断依据</summary><p class="mono break">包内作者：${esc(i.evidence.creatorName||tr('未填写'))}<br>包内包名：${esc(i.evidence.packageName||tr('未填写'))}<br>${i.evidence.paths.slice(0,6).map(esc).join('<br>')}</p></details>`:''}${type==='json'&&i.repairable?ui`<details><summary>查看 JSON 修改差异</summary><pre class="repair-diff">${esc(i.diff)}</pre></details>`:''}</div></div>`).join('');
  const groups=[['name',tr('文件名格式')],['json',tr('meta.json 语法')]].map(([type,title])=>`<h3 class="spaced">${title} · ${p.items.filter(i=>i.type===type).length}</h3>${rows(type)||ui('<p class="muted spaced">没有此类修复建议。</p>')}`).join('');
  openModal(ui`<div class="name-repair-modal"><h2>预览包异常修复</h2><p>简介模型：${esc(p.model)} · ${p.items.filter(i=>i.repairable).length} 项可采用建议</p><div class="notice spaced">勾选后才修改文件。JSON 修复仅替换 meta.json，保留其他 ZIP 资源原始压缩数据；临时重建需要相当于当前单个包大小的空闲空间。恢复记录只保留原元数据和 ZIP 目录，不长期备份整包。</div><label class="checkline spaced"><input type="checkbox" data-package-repair-all checked>选择全部可用建议</label><div class="name-repair-list">${groups}<details class="spaced repair-skipped"><summary>其他问题 / 暂不支持 · ${p.skipped.length}</summary>${p.skipped.map(i=>`<p class="spaced"><span class="mono break">${esc(areas[i.area])} / ${esc(i.rel)}</span><br>${esc(i.reason)}</p>`).join('')||ui('<p>无其他异常。</p>')}</details></div><div class="modal-actions"><button class="button" data-action="close-modal">取消</button><button class="button primary" data-action="package-repair-apply" ${p.items.some(i=>i.repairable)?'':'disabled'}>执行所选修复</button></div></div>`);
}
function showNameRepairPreview(p){
  state.nameRepair=p;
  openModal(ui`<div class="name-repair-modal"><h2>预览包文件名修复</h2><p>简介模型：${esc(p.model)} · 共 ${fmt(p.items.length)} 个建议${p.skipped?tr`，跳过 ${fmt(p.skipped)} 个其他异常包`:''}。目标格式：作者.包名.整数版本.var。</p><div class="notice spaced">版本号不明确时属于推测，请核对黄色提示。改名后 VaM 使用新包名识别资源；包内文件保持原样。与已有包同名时分别保留；VaM 对同名 UID 只采用一份，可在启动方案选择副本。</div><label class="checkline spaced"><input type="checkbox" data-name-repair-all checked>选择全部建议</label><div class="name-repair-list">${p.items.map(x=>ui`<div class="name-repair-row" data-repair-id="${x.id}"><input type="checkbox" data-name-repair-select checked aria-label="选择修复 ${esc(x.rel)}"><div><div class="mono break">${esc(x.rel)}</div><label class="form-row"><span>新文件名</span><input data-name-repair-filename value="${esc(x.filename)}" aria-label="新文件名 ${x.id}" spellcheck="false"></label><p class="muted">${esc(x.reason)}</p><span class="badge ${x.confidence==='high'?'green':'orange'}">${{high:tr('依据明确'),medium:tr('需要核对'),low:tr('推测名称')}[x.confidence]}</span>${x.conflict?ui('<span class="badge orange">已有同名包 · 将分别保留</span>'):''}<details class="spaced"><summary>查看判断依据</summary><p class="mono break">包内作者：${esc(x.evidence.creatorName||tr('未填写'))}<br>包内包名：${esc(x.evidence.packageName||tr('未填写'))}</p><p class="mono break">${x.evidence.paths.slice(0,6).map(esc).join('<br>')}</p></details></div></div>`).join('')}</div><div class="modal-actions"><button class="button" data-action="close-modal">取消</button><button class="button primary" data-action="name-repair-apply" ${!p.items.length?'disabled':''}>执行所选修复</button></div></div>`);
}

document.addEventListener('click',async e=>{
  const summaryToggle=e.target.closest('[data-summary-toggle]');if(summaryToggle){summaries.toggle(summaryToggle);return;}
  const modelRefresh=e.target.closest('[data-refresh-models]');if(modelRefresh){await modelPicker.load(modelRefresh.dataset.refreshModels,true);return;}
  const b=e.target.closest('button,a.brand');
  if(!b){
    const row=e.target.closest('.package-row[data-key],.recommendation-card[data-key]');
    if(row&&!e.target.closest('input,label,a,select,textarea,[contenteditable]')&&!getSelection()?.toString().trim()){
      try{await detail(row.dataset.key);}catch(err){toast(err.message,true);}
    }
    return;
  }
  try{
    if(b.dataset.paletteChoice||b.dataset.modeChoice||b.dataset.action==='toggle-theme'){
      const patch=b.dataset.paletteChoice?{palette:b.dataset.paletteChoice}:b.dataset.modeChoice?{mode:b.dataset.modeChoice}:
        {mode:document.documentElement.dataset.mode==='dark'?'light':'dark'};
      return await VaMPMTheme.change(patch,value=>api('appearance',value));
    }
    if(b.hasAttribute('data-storage-migrate'))return await startJob('game-storage/migrate',{});
    if(b.hasAttribute('data-directory-browse'))return await browseDirectory(b);
    if(b.dataset.workspaceSwitch)return await switchWorkspace(b.dataset.workspaceSwitch);
    if(b.dataset.workspaceRename)return renameDirectory(b.dataset.workspaceRename);
    if(b.dataset.workspaceRebind)return rebindDirectory(b.dataset.workspaceRebind);
    if(b.dataset.workspaceRemove)return removeDirectory(b.dataset.workspaceRemove);
    if(homeEditorClick(b))return;
    if(quarantine.owns(b))return await quarantine.click(b);
    if(chatPresets.owns(b))return await chatPresets.click(b);
    if(b.matches('.brand')){e.preventDefault();return await changePage('home');}
    if(b.dataset.homeLibrary!==undefined)return await homeLibrary(b.dataset.homeLibrary);
    if(b.dataset.homeCategory!==undefined)return await homeLibrary('',b.dataset.homeCategory);
    if(b.dataset.homeSettings)return await homeSettings(b.dataset.homeSettings);
    if(b.dataset.view){if(state.view===b.dataset.view)return;state.view=b.dataset.view;rememberLibrary({view:state.view});return await render();}
    if(b.dataset.page){if(b.dataset.page==='library'&&favoriteView())state.query={q:'',category:'',state:'',scope:'',sort:state.query.sort,page:1};return await changePage(b.dataset.page);}
    if(b.dataset.category!==undefined){state.query.category=b.dataset.category;state.query.page=1;return await render();}
    if(b.dataset.detail)return await detail(b.dataset.detail);
    if(b.dataset.favorite){b.disabled=true;return await startJob('favorite',{keys:[b.dataset.favorite],favorite:b.dataset.value==='true'});}
    if(b.dataset.removeSelection){state.selected.delete(b.dataset.removeSelection);persist();return await render();}
    if(b.dataset.cancelJob){await api('cancel',{id:b.dataset.cancelJob});toast(tr('正在停止，等待当前步骤结束…'));return;}
    if(b.dataset.undo){await startJob('undo',{id:b.dataset.undo});return;}
    const a=b.dataset.action;
    if(a==='base-packages'){state.query={q:'',category:'',state:'pinned',scope:'',sort:state.query.sort,page:1};return await changePage('library');}
    if(a==='pin-selection'||a==='unpin-selection')return await startJob('plan',{kind:a==='pin-selection'?'pin':'unpin',keys:[...state.selected]});
    if(a==='detail-pin')return await startJob('plan',{kind:state.detail.pin_root?'unpin':'pin',keys:[state.detail.key]});
    if(a==='favorites'){state.query={q:'',category:'',state:'',scope:'favorites',sort:state.query.sort,page:1};return await changePage('library');}
    if(a==='ai-all')return summaryOptions(favoriteView()?'favorites':'all');
    if(a==='confirm-summary'){const mode=$('input[name=summary-mode]:checked')?.value;if(!mode||!state.summaryRequest)return;const request={...state.summaryRequest,mode,target:$('input[name=summary-target]:checked')?.value||'both'};state.summaryRequest=null;closeModal();try{return await startJob('ai-queue/start',request);}finally{await refresh();}}
    if(a==='dismiss-summary'){
      const q=state.summaryQueue;
      if(q?.status==='done'&&q.run_id){
        b.disabled=true;
        try{
          const saved=await api('ai-queue/dismiss',{run_id:q.run_id});
          if(state.summaryQueue?.run_id===q.run_id)state.summaryQueue=saved;
          paintSummaryQueue();
        }finally{if(b.isConnected)b.disabled=false;}
      }
      return;
    }
    if(a==='pause-summary'){state.summaryQueue=await api('ai-queue/pause',{});paintSummaryQueue();toast(tr('将在当前批次保存后暂停。'));return;}
    if(a==='resume-summary'){await startJob('ai-queue/resume',{});await refresh();return;}
    if(a==='finish-summary'){state.summaryQueue=await api('ai-queue/finish',{});paintSummaryQueue();toast(tr('本轮已结束，已保存的简介保留。'));return;}
    if(a==='favorite-selection')return await startJob('favorite',{keys:[...state.selected],favorite:true});
    if(a==='source-profile')return await sourceProfile();
    if(a==='chat-stop'){await api('agent/stop',{});toast(tr('正在停止助手任务…'));return;}
    if(a==='chat-new'){const r=await api('agent/reset',{});mergeChat(r);paintChat();return;}
    if(a==='scan'||a==='deep-scan')return await startJob('scan',{deep:a==='deep-scan'});
    if(a==='launch'){await api('launch',{});toast(tr('VaM 已启动，使用当前启用的包。'));return;}
    if(a==='retry')return await render();
    if(a==='close-modal')return closeModal();
    if(a==='close-drawer')return closeDrawer();
    if(a==='select-results'){
      const signature=querySignature(),revision=state.revision;
      if(state.itemsQuery!==signature)throw Error(tr('筛选结果正在更新，请稍后再选择。'));
      const r=await api('package-keys?'+new URLSearchParams(state.query));
      if(state.page!=='library'||signature!==querySignature()||revision!==state.revision)throw Error(tr('筛选或资源已变化，请重新选择当前结果。'));
      r.keys.forEach(k=>state.selected.add(k));updateSelection();return;
    }
    if(a==='clear-selection'){state.selected.clear();updateSelection();return;}
    if(['library-page','dep-page','selection-page'].includes(a))return await changePagination(a,b.dataset.value);
    if(a==='audit')return await startJob('audit',{});
    if(a==='package-repair-last')return showPackageRepairPreview(await api('package-repairs/preview'));
    if(a==='package-repair-analyze')return await startJob('package-repairs/analyze',{refresh:b.dataset.refresh==='true'});
    if(a==='package-repair-apply'){
      const items=$$('.name-repair-row[data-repair-type]').filter(row=>$('[data-package-repair-select]',row).checked).map(row=>({type:row.dataset.repairType,id:Number(row.dataset.repairId),...(row.dataset.repairType==='name'?{filename:$('[data-name-repair-filename]',row).value.trim()}:{})}));
      if(!items.length)throw Error(tr('请选择需要修复的项目。'));
      const id=state.packageRepair.id;closeModal();return await startJob('package-repairs/apply',{id,items});
    }
    if(a==='name-repair-last')return showNameRepairPreview(await api('name-repairs/preview'));
    if(a==='name-repair-analyze')return await startJob('name-repairs/analyze',{refresh:b.dataset.refresh==='true'});
    if(a==='name-repair-apply'){
      const items=$$('.name-repair-row').filter(row=>$('[data-name-repair-select]',row).checked).map(row=>({id:Number(row.dataset.repairId),filename:$('[data-name-repair-filename]',row).value.trim()}));
      if(!items.length)throw Error(tr('请选择需要修复的包。'));
      const id=state.nameRepair.id;closeModal();return await startJob('name-repairs/apply',{id,items});
    }
    if(a==='dedup')return await startJob('plan',{kind:'dedup'});
    if(a==='organize-all')return await startJob('plan',{kind:'organize',scope:b.dataset.archiveScope});
    if(a==='organize')return await startJob('plan',{kind:'organize',keys:[...state.selected]});
    if(a==='activate')return await startJob('plan',{kind:'activate',keys:[...state.selected]});
    if(a==='restore-all')return await startJob('plan',{kind:'restore_all'});
    if(a==='stage-all')return await startJob('plan',{kind:'stage_all'});
    if(a==='update-activation-preview'){
      const request=VaMPMActivation.request(state.plan,$('#dialog'));
      closeModal();return await startJob('plan',request);
    }
    if(a==='apply-plan'){const id=state.plan.id;closeModal();return await startJob('apply',{id});}
    if(a==='source-settings')return await sourceSettings();
    if(a==='remove-source'){const current=await api('source-settings');await api('source-settings',{sites:current.sites.filter(x=>x.id!==b.dataset.source)});return await sourceSettings();}
    if(a==='find-deps')return await startJob('find-dependencies',{});
    if(a==='download-deps'){const s=state.sources;openModal(ui`<h2>下载可补充的依赖</h2><p>本次共 ${s.ready.length} 个 VAR，约 ${bytes(s.ready.reduce((a,x)=>a+x.bytes,0))}。下载后自动更新索引并按当前方案同步启停；所需依赖启用，其余进入停用库的对应分类。</p><div class="notice">下载官方 Hub、作者 GitHub 或你添加的可信来源，完整 VAR 即使作者、名称、体积或哈希与发布信息不一致也会保留，并以感叹号标注；元数据无法读取的包保留为异常。未下完整或并非 VAR 的响应仍会报错。</div><div class="modal-actions"><button class="button" data-action="close-modal">取消</button><button class="button primary" data-action="confirm-download">开始下载</button></div>`);return;}
    if(a==='confirm-download'){closeModal();return await startJob('install-dependencies',{});}
    if(a==='save-profile'){await api('profile',{name:$('#profile-name').value,keys:[...state.selected]});toast(tr('启动方案已保存'));return await render();}
    if(['use-profile','load-profile','delete-profile'].includes(a)){
      const list=await api('profiles'),p=list.find(x=>x.id===b.dataset.profile);if(!p)throw Error(tr('方案不存在'));
      if(a==='use-profile')return await startJob('plan',{kind:'activate',profile_id:p.id});
      if(a==='load-profile'){state.selected=new Set(p.keys);persist();toast(['favorites','folder'].includes(p.source?.type)?tr('已复制当前名单为固定选择；需要自动更新时，请使用右侧原方案的「预览启用」。'):tr('已载入方案选择，可以继续添加或移除包'));return await render();}
      if(a==='delete-profile'){await api('delete-profile',{id:p.id});return await render();}
    }
    if(a==='ai-selection')return summaryOptions('selected',[...state.selected]);
    if(a==='detail-select'){const k=state.detail.key;state.selected.has(k)?state.selected.delete(k):state.selected.add(k);updateSelection();return await detail(k);}
    if(a==='detail-ai'){const key=state.detail.key;closeDrawer();return summaryOptions('selected',[key]);}
    if(a==='detail-folder'){await api('reveal',{key:state.detail.key,...(b.dataset.fileId?{file_id:Number(b.dataset.fileId)}:{})});return;}
    if(a==='codex-status'){b.disabled=true;const r=await api('codex-status');const el=$('#codex-result');el.classList.remove('hidden');el.textContent=(r.ok?tr('已检测到本地登录记录（请求时还会验证凭据有效性）：'):'')+msg(r.message)+(r.path?' · '+r.path:'');b.disabled=false;return;}

    if(a==='codex-login'){await api('codex-login',{});toast(tr('已打开 Codex 登录，请在弹出的页面完成。'));return;}
    if(a==='open-folder'){await api('reveal',{});return;}
    if(a==='shutdown'){await api('shutdown',{});stopped=true;pageConnection.close();clearTimeout(pollTimer);$('#main').innerHTML=empty(tr('包管家正在退出'),tr('正在停止任务并保存记录，可以关闭此页面；下次双击 EXE 即可重新打开。'));return;}
    if(a==='import')return await showImport();
    if(a==='import-start')return await importStart();
    if(a==='import-cancel'){if(importTransfer){importTransfer.cancelled=true;b.disabled=true;$('#upload-progress').textContent=tr('正在停止：当前文件复制完成后清理暂存文件。');}return;}
    if(a==='import-to-deps'){closeModal();return await changePage('dependencies');}
  }catch(err){toast(err.message,true);b.disabled=false;}
});
document.addEventListener('change',async e=>{
  const el=e.target;
  if(['import-files','import-folder'].includes(el.id)){
    if(el.files.length)$(el.id==='import-files'?'#import-folder':'#import-files').value='';
    showImportSelection();return;
  }
  if(VaMPMActivation.changed(el,$('#dialog')))return;
  if(el.dataset.select){el.checked?state.selected.add(el.dataset.select):state.selected.delete(el.dataset.select);updateSelection();}
  if(el.matches('[data-workspace-select]')){try{await switchWorkspace(el.value);}catch(error){el.value=state.workspace.id;toast(error.message,true);}return;}
  quarantine.change(el);
  if(el.dataset.homeEnabled){homeDraft.enabled[el.dataset.homeEnabled]=el.checked;return;}
  if(el.id==='profile-source')$('#folder-field').classList.toggle('hidden',el.value!=='folder');
  if(el.id==='select-page'){
    if(state.itemsQuery!==querySignature()){updateSelection();return toast(tr('筛选结果正在更新，请稍后再选择。'));}
    state.items.forEach(p=>el.checked?state.selected.add(p.key):state.selected.delete(p.key));updateSelection();
  }
  if(el.id==='state-filter'){if(el.value==='favorite'){state.query.scope='favorites';state.query.state='';}else state.query.state=el.value;state.query.page=1;await render();}
  if(el.id==='sort-filter'){state.query.sort=el.value;state.query.page=1;rememberLibrary({sort:state.query.sort});await render();}
  if(el.id==='ai-provider'){$('#codex-settings').classList.toggle('hidden',el.value!=='codex');$('#api-settings').classList.toggle('hidden',el.value==='codex');}
});
function searchInput(e){
  if(e.target.id==='profile-folder'){clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadFolderChoices(e.target.value).catch(err=>toast(err.message,true)),180);}
  if(e.target.id==='chat-input')chat.draft=e.target.value;
  if(e.target.id==='profile-name')state.profileName=e.target.value;
  if(e.target.id==='search'||e.target.id==='dependency-search'){
    const id=e.target.id,value=e.target.value,position=e.target.selectionStart;
    if(id==='search'){state.query.q=value;state.query.page=1;}else{state.depQ=value;state.depPage=1;}
    clearTimeout(searchTimer);renderController?.abort();state.renderId++;
    if(e.isComposing)return;
    searchTimer=setTimeout(async()=>{await render(id==='search');if(id!=='search'){const el=$('#'+id);if(el){el.focus();el.setSelectionRange(position,position);}}},120);
  }
}
document.addEventListener('input',searchInput);
document.addEventListener('compositionend',searchInput);
document.addEventListener('submit',async e=>{
  if(e.target.dataset.pagerForm){e.preventDefault();try{await changePagination(e.target.dataset.pagerForm,$('input',e.target).value);}catch(err){toast(err.message,true);}return;}
  if(e.target.id==='chat-form'){e.preventDefault();try{await sendChat();}catch(err){toast(err.message,true);}return;}
  if(!['directory-add-form','directory-rename-form','directory-folders-form','directory-rebind-form','directory-remove-form','home-layout-form','home-search','settings-form','label-form','source-form','profile-source-form'].includes(e.target.id))return;e.preventDefault();
  try{if(['directory-add-form','directory-rename-form','directory-rebind-form'].includes(e.target.id)&&!validateDirectoryForm(e.target)){e.target.reportValidity();return;}const form=Object.fromEntries(new FormData(e.target));
    if(e.target.id==='directory-rebind-form'||e.target.id==='directory-remove-form'){const out=await api(e.target.id==='directory-rebind-form'?'game-directories/rebind':'game-directories/remove',{...form,id:form.directory_id});if(out.workspace.epoch!==state.workspace?.epoch){reloadWorkspace();return;}state.directories=out.directories;closeModal();await refresh();await render();toast(tr('目录已更新，原数据保留'));return;}
    if(e.target.id==='directory-folders-form'){await api('game-directories/library-folders',form);reloadWorkspace();return;}
    if(e.target.id==='directory-add-form'||e.target.id==='directory-rename-form'){state.directories=await api(e.target.id==='directory-add-form'?'game-directories/add':'game-directories/rename',{...form,id:form.directory_id});closeModal();await refresh();await render();toast(tr('目录已保存'));return;}
    if(e.target.id==='home-layout-form'){state.homeLayout=await api('home-layout',{cards:homeDraft.order.filter(key=>homeDraft.enabled[key])});closeModal();await render();toast(tr('主页布局已保存'));return;}
    if(e.target.id==='home-search')return await homeLibrary('','',form.q.trim());
    if(e.target.id==='profile-source-form'){await api('profile',{name:form.name,...(form.source==='favorites'?{favorites:true}:{folder:form.folder})});closeModal();toast(tr('动态启动方案已保存，依赖自动包含'));return await changePage('profiles');}
    if(e.target.id==='source-form'){const current=await api('source-settings');await api('source-settings',{sites:[...current.sites,form]});toast(tr('下载来源已添加，重新查找依赖即可使用'));await sourceSettings();}
    else if(e.target.id==='settings-form'){for(const k of ['clear_key','summary_images'])form[k]=!!form[k];modelPicker.settings(form);await api('settings',form);toast(tr('AI 设置已保存'));await render();}
    else{form.tags=(form.tags||'').split(/[，,、\n]/).map(x=>x.trim()).filter(Boolean);await api('label',{key:state.detail.key,...form});toast(tr('简介、分类与标签已保存'));await refresh();await detail(state.detail.key);if(['home','library'].includes(state.page))await render();}
  }catch(err){toast(err.message,true);}
});
$('#drawer-backdrop').addEventListener('click',e=>{if(e.target.id==='drawer-backdrop')closeDrawer();});
$('#dialog').addEventListener('cancel',e=>{if(importTransfer)e.preventDefault();});
document.addEventListener('keydown',e=>{if(e.target.matches('.package-row[data-key],.recommendation-card[data-key]')&&['Enter',' '].includes(e.key)){e.preventDefault();detail(e.target.dataset.key).catch(err=>toast(err.message,true));return;}if(e.key==='Escape')closeDrawer();if(e.target.id==='chat-input'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendChat().catch(err=>toast(err.message,true));}});
(async()=>{
  try{const s=await refresh();s.jobs.filter(j=>j.status!=='running').forEach(j=>state.seenJobs.add(j.id));await render();firstRun.start(s);poll();}
  catch(e){$('#main').innerHTML=empty(tr('请通过 EXE 打开包管家'),esc(e.message)+ui('<br>双击工具目录中的「VaMPackageManager.exe」。'));}
})();

// Reload only after an explicit language change; no resource data is rewritten.
document.addEventListener('change',async event=>{
  if(event.target.id!=='interface-language')return;
  const select=event.target;select.disabled=true;
  try{
    await api('language',{language:select.value});
    sessionStorage.setItem('vampm-language-page',state.page);
    reloadWorkspace();
  }catch(error){select.value=VaMPMI18n.locale;select.disabled=false;toast(error.message,true);}
});
