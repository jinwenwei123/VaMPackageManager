'use strict';

// A live HTTP stream survives background-tab timer throttling. No polling lease expires
// just because the user switches to VaM or minimizes the browser.
class PageConnection {
  constructor(token) {
    this.token=token;this.id=crypto.randomUUID();this.controller=null;this.timer=null;this.closed=false;
    window.addEventListener('pagehide',()=>this.close());
    window.addEventListener('pageshow',event=>{
      if(event.persisted){this.id=crypto.randomUUID();this.closed=false;this.start();}
    });
  }
  async start() {
    if(this.closed||this.controller||!this.token)return;
    const controller=new AbortController();this.controller=controller;
    try {
      const response=await fetch('/api/ui-session?id='+encodeURIComponent(this.id),{
        headers:{'X-Session-Token':this.token},signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw Error('Page connection ended');
      const reader=response.body.getReader();
      try{while(!(await reader.read()).done){/* The connection itself owns the session. */}}
      finally{reader.releaseLock();}
    }catch{/* The ordinary status poll explains a disconnected application. */}
    finally {
      if(this.controller===controller){
        this.controller=null;
        if(!this.closed)this.timer=setTimeout(()=>this.start(),1000);
      }
    }
  }
  close() {
    if(this.closed)return;
    this.closed=true;clearTimeout(this.timer);
    // keepalive reaches the server after the document has been discarded; the stream
    // disconnect also covers crashes where pagehide never runs.
    if(this.token)fetch('/api/ui-session/close',{method:'POST',keepalive:true,
      headers:{'X-Session-Token':this.token,'Content-Type':'application/json'},
      body:JSON.stringify({id:this.id})}).catch(()=>{});
    this.controller?.abort();
  }
}
