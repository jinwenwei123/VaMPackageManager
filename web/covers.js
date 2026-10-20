'use strict';
// At most four archive reads at once; only visible cards are decoded. Object URLs
// are bounded and released when no longer displayed, including empty previews.
window.PackageCovers=class {
  constructor(token){this.token=token;this.cache=new Map();this.pending=new Map();this.queue=[];this.active=0;this.observer=null;}
  key(el){return (window.VaMPMWorkspace?.epoch||'')+'|'+el.dataset.cover+'|'+(el.dataset.coverVersion||'');}
  watch(root=document){
    this.observer?.disconnect();this.queue=[];
    this.observer=new IntersectionObserver(entries=>{
      for(const entry of entries)if(entry.isIntersecting){
        this.observer.unobserve(entry.target);this.queue.push(entry.target);
      }
      this.drain();
    },{rootMargin:'180px'});
    root.querySelectorAll('[data-cover]:not([data-cover-done])').forEach(el=>this.observer.observe(el));
    this.trim();
  }
  drain(){while(this.active<4&&this.queue.length){const el=this.queue.shift();if(!el.isConnected)continue;
    this.active++;this.show(el).catch(()=>{}).finally(()=>{this.active--;this.drain();});}}
  async load(el){
    const key=this.key(el);
    if(this.cache.has(key)){const value=this.cache.get(key);this.cache.delete(key);this.cache.set(key,value);return value;}
    if(this.pending.has(key))return this.pending.get(key);
    const promise=(async()=>{
      const response=await fetch('/api/thumbnail?'+new URLSearchParams({key:el.dataset.cover,v:el.dataset.coverVersion||'',workspace:window.VaMPMWorkspace?.epoch||''}),{headers:{'X-Session-Token':this.token}});
      if(!response.ok)throw Error(tr('封面读取失败'));
      let url=null;
      if(response.status!==204){
        let blob=await response.blob();
        // Bound decoded image memory as well as network concurrency. No library
        // or game image is changed; this is a disposable browser preview.
        if(window.createImageBitmap){
          const bitmap=await createImageBitmap(blob);
          try{const ratio=Math.min(1,640/Math.max(bitmap.width,bitmap.height));
            const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
            canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
            blob=await new Promise(resolve=>canvas.toBlob(b=>resolve(b||blob),'image/webp',.87));
          }finally{bitmap.close();}
        }
        url=URL.createObjectURL(blob);
      }
      this.cache.set(key,url);return url;
    })();
    this.pending.set(key,promise);
    try{return await promise;}finally{this.pending.delete(key);}
  }
  async show(el){
    try{const url=await this.load(el);if(!el.isConnected)return;
      if(url){const img=document.createElement('img');img.alt=el.dataset.coverLabel||tr('资源包封面');img.decoding='async';img.src=url;
        el.replaceChildren(img);el.classList.add('has-cover');}
      else el.title=tr('包内没有可用预览图');
      el.dataset.coverDone='1';
    }catch{if(el.isConnected){el.dataset.coverDone='1';el.title=tr('暂无可用预览图');}}
    finally{this.trim();}
  }
  trim(){
    const visible=new Set([...document.querySelectorAll('[data-cover]')].map(el=>this.key(el)));
    for(const [key,url] of this.cache){if(this.cache.size<=120)break;if(visible.has(key))continue;
      if(url)URL.revokeObjectURL(url);this.cache.delete(key);}
  }
};
