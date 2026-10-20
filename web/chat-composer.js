'use strict';

class ChatComposerResize {
  constructor(){
    this.height=48;this.form=null;this.drag=null;this.frame=0;
    try{const saved=Number(localStorage.getItem('vampm-chat-input-height'));if(Number.isFinite(saved)&&saved>=48)this.height=Math.min(480,saved);}catch{}
    this.resize=new ResizeObserver(()=>{
      cancelAnimationFrame(this.frame);this.frame=requestAnimationFrame(()=>this.paint());
    });
  }
  bind(){
    const form=document.querySelector('#chat-form');
    if(form===this.form){this.paint();return;}
    this.finish();this.events?.abort();this.resize.disconnect();
    this.form=form;if(!form)return;
    this.input=form.querySelector('#chat-input');this.handle=form.querySelector('.chat-resize-handle');
    this.shell=form.closest('.chat-shell');this.events=new AbortController();
    const options={signal:this.events.signal};
    this.handle.addEventListener('pointerdown',event=>{
      if(event.button!==0||!event.isPrimary)return;
      event.preventDefault();this.handle.focus({preventScroll:true});
      this.drag={id:event.pointerId,y:event.clientY,height:this.input.getBoundingClientRect().height};
      this.handle.setPointerCapture(event.pointerId);document.body.classList.add('chat-resizing');
    },options);
    this.handle.addEventListener('pointermove',event=>{
      if(this.drag?.id===event.pointerId)this.adjust(this.drag.height+this.drag.y-event.clientY);
    },options);
    for(const type of ['pointerup','pointercancel','lostpointercapture'])this.handle.addEventListener(type,()=>this.finish(),options);
    this.handle.addEventListener('keydown',event=>{
      const current=this.input.getBoundingClientRect().height;
      const values={ArrowUp:current+16,ArrowDown:current-16,Home:48,End:this.maximum()};
      if(!(event.key in values))return;
      event.preventDefault();this.adjust(values[event.key]);this.save();
    },options);
    for(const node of [this.shell,this.shell.querySelector('.chat-topline'),this.shell.querySelector('.chat-suggestions'),form.querySelector('.chat-actions')])if(node)this.resize.observe(node);
    this.paint();
  }
  maximum(){
    const fixed=['.chat-topline','.chat-suggestions'].reduce((sum,selector)=>sum+(this.shell.querySelector(selector)?.offsetHeight||0),0);
    const controls=this.form.offsetHeight-this.input.offsetHeight;
    const thread=Math.min(160,Math.floor(this.shell.clientHeight*.35));
    return Math.max(48,Math.min(480,Math.floor(this.shell.clientHeight-fixed-controls-thread)));
  }
  paint(){
    if(!this.form?.isConnected)return;
    const max=this.maximum(),height=Math.max(48,Math.min(max,Math.round(this.height)));
    this.input.style.height=height+'px';
    this.handle.setAttribute('aria-valuemax',String(max));
    this.handle.setAttribute('aria-valuenow',String(height));
    this.handle.setAttribute('aria-valuetext',height+tr(' 像素'));
  }
  adjust(height){this.height=Math.max(48,Math.min(this.maximum(),Math.round(height)));this.paint();}
  save(){try{localStorage.setItem('vampm-chat-input-height',String(this.height));}catch{}}
  finish(){
    if(!this.drag)return;
    const id=this.drag.id;this.drag=null;
    if(this.handle.hasPointerCapture(id))this.handle.releasePointerCapture(id);
    document.body.classList.remove('chat-resizing');this.save();
  }
}
