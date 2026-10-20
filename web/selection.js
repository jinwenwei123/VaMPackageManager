'use strict';

// Keep one toolbar, reserve its original space and float only after scrolling past it.
window.SelectionDock=class SelectionDock {
  constructor(){
    this.slot=null;this.bar=null;this.frame=0;
    this.queue=()=>{if(!this.frame)this.frame=requestAnimationFrame(()=>{this.frame=0;this.update();});};
    this.resize=new ResizeObserver(this.queue);
    window.addEventListener('scroll',this.queue,{passive:true});
    window.addEventListener('resize',this.queue,{passive:true});
    window.visualViewport?.addEventListener('resize',this.queue,{passive:true});
    window.visualViewport?.addEventListener('scroll',this.queue,{passive:true});
  }
  bind(){
    const slot=document.querySelector('#selection-slot');
    if(slot===this.slot){this.queue();return;}
    this.resize.disconnect();this.slot=slot;this.bar=slot?.querySelector('#selection-bar');
    if(!this.bar)return;
    this.resize.observe(this.slot);this.resize.observe(this.bar);this.update();
  }
  update(){
    if(!this.slot?.isConnected||!this.bar||this.slot.classList.contains('hidden'))return;
    const rect=this.slot.getBoundingClientRect(),top=(window.visualViewport?.offsetTop||0)+12;
    this.bar.style.setProperty('--selection-left',rect.left+'px');
    this.bar.style.setProperty('--selection-width',rect.width+'px');
    this.bar.style.setProperty('--selection-top',top+'px');
    this.bar.classList.toggle('is-floating',rect.top<top);
    this.slot.style.height=this.bar.offsetHeight+'px';
  }
};
