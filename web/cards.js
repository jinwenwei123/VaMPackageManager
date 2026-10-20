'use strict';
class PackageSummaries {
  constructor(){
    this.expanded=new Set();this.sequence=0;this.frame=0;
    this.resize=new ResizeObserver(()=>this.sync());
    this.resize.observe(document.querySelector('#main'));
    document.fonts?.ready.then(()=>this.sync());
  }
  html(packageInfo,scope){
    const key=scope+':'+packageInfo.key,expanded=this.expanded.has(key),id='card-summary-'+(++this.sequence);
    return `<div class="summary-wrap" data-summary-id="${esc(key)}" data-expanded="${expanded}"><div class="package-summary" id="${id}">${esc(packageInfo.summary)}</div><button type="button" class="summary-toggle" data-summary-toggle aria-controls="${id}" aria-expanded="${expanded}" aria-label="${expanded?tr('收起简介'):tr('展开简介')}" title="${expanded?tr('收起简介'):tr('展开简介')}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button></div>`;
  }
  sync(){
    cancelAnimationFrame(this.frame);
    this.frame=requestAnimationFrame(()=>{
      const rows=[...document.querySelectorAll('.grid-view .summary-wrap,.recommendation-card .summary-wrap')];
      // Batch geometry reads before attribute writes; at most the visible page
      // and conversation cards, never the entire library.
      const states=rows.map(row=>row.dataset.expanded==='true'||row.querySelector('.package-summary').scrollHeight>row.querySelector('.package-summary').clientHeight+1);
      rows.forEach((row,i)=>row.dataset.collapsible=String(states[i]));
    });
  }
  toggle(button){
    const row=button.closest('.summary-wrap'),expanded=row.dataset.expanded!=='true';
    row.dataset.expanded=String(expanded);
    if(expanded)this.expanded.add(row.dataset.summaryId);else this.expanded.delete(row.dataset.summaryId);
    button.setAttribute('aria-expanded',String(expanded));
    button.setAttribute('aria-label',expanded?tr('收起简介'):tr('展开简介'));button.title=expanded?tr('收起简介'):tr('展开简介');
    this.sync();
  }
}

class PackageTags {
  html(p,all=false){
    const tags=p.tags||[],visible=all?tags:tags.slice(0,3),remaining=tags.length-visible.length;
    if(!tags.length)return all?'':ui('<div class="content-tags content-tags-preview" aria-label="内容标签"><span class="tag-placeholder">暂无标签</span></div>');
    return ui`<div class="content-tags ${all?'':'content-tags-preview'}" aria-label="内容标签">${visible.map(tag=>`<span class="content-tag" title="${esc(tag)}">${esc(tag)}</span>`).join('')}${remaining?ui`<span class="tags-more" aria-label="另有${remaining}个标签" title="另有${remaining}个标签，进入详情查看">+${remaining}</span>`:''}</div>`;
  }
}
