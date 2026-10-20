'use strict';

// One real control and one reserved slot: floating never duplicates focus targets.
window.PagerDock = class PagerDock {
  constructor(){
    this.slot=null;this.bar=null;this.frame=0;
    this.queue=()=>{if(!this.frame)this.frame=requestAnimationFrame(()=>{this.frame=0;this.update();});};
    this.intersection=new IntersectionObserver(this.queue,{threshold:[0,1],rootMargin:'0px 0px -18px 0px'});
    this.resize=new ResizeObserver(this.queue);
    window.addEventListener('resize',this.queue,{passive:true});
    window.visualViewport?.addEventListener('resize',this.queue,{passive:true});
    window.visualViewport?.addEventListener('scroll',this.queue,{passive:true});
  }
  bind(){
    const slot=document.querySelector('[data-floating-pager]');
    if(slot===this.slot){this.queue();return;}
    this.intersection.disconnect();this.resize.disconnect();
    this.slot=slot;this.bar=slot?.querySelector('.pagination')||null;
    if(!slot)return;
    this.intersection.observe(slot);this.resize.observe(slot);
    this.resize.observe(document.querySelector('#main'));
    this.update();
  }
  update(){
    if(!this.slot?.isConnected||!this.bar)return;
    const rect=this.slot.getBoundingClientRect(),main=document.querySelector('#main').getBoundingClientRect();
    const viewport=window.visualViewport,left=viewport?.offsetLeft||0,top=viewport?.offsetTop||0;
    const width=viewport?.width||innerWidth,height=viewport?.height||innerHeight;
    const edge=top+height-18,lo=Math.max(left+12,main.left),hi=Math.min(left+width-12,main.right);
    this.bar.style.setProperty('--pager-left',((lo+hi)/2)+'px');
    this.bar.style.setProperty('--pager-width',Math.max(0,hi-lo-24)+'px');
    this.bar.style.setProperty('--pager-bottom',(innerHeight-edge)+'px');
    this.bar.classList.toggle('is-floating',Number(this.slot.dataset.count)>1&&rect.bottom>edge+1);
  }
};
