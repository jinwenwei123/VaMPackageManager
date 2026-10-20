'use strict';
// Only source-owned strings enter these helpers. Interpolated package names,
// paths, summaries, tags and messages from the conversation remain untouched.
window.VaMPMI18n = (() => {
  const locale = window.VaMPMLanguageConfig?.language === 'en' ? 'en' : 'zh-CN';
  const catalog = window.VaMPMEnglish || {};
  const messages = window.VaMPMMessagesEnglish || {};
  const han = /[\u3400-\u9fff]/;
  const slots = /\{(\d+)\}/g;
  const missing = new Set();
  const escapeHTML = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function phrase(source, html=false) {
    if (locale !== 'en' || !han.test(source)) return source;
    const match = source.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const indices = [];
    const key = match[2].replace(slots, (_,i) => {
      let at = indices.indexOf(i);
      if (at < 0) { at = indices.length; indices.push(i); }
      return '{'+at+'}';
    });
    const value = catalog[key];
    if (value === undefined) { missing.add(key); return source; }
    const translated = html ? escapeHTML(value) : value;
    return match[1]+translated.replace(slots,(_,i)=>'{'+indices[Number(i)]+'}')+match[3];
  }
  function template(strings) {
    return typeof strings === 'string' ? strings :
      strings.reduce((text,part,i)=>text+(i?'{'+(i-1)+'}':'')+part,'');
  }
  const htmlCache = new Map(), textCache = new Map();
  function render(strings, values, html) {
    const cache = html ? htmlCache : textCache;
    let compiled = cache.get(strings);
    if (compiled === undefined) {
      const source = template(strings);
      compiled = html ? source.replace(/<[^>]*>|[^<]+/g, part => part.startsWith('<')
        ? part.replace(/\b(title|aria-label|aria-valuetext|placeholder|alt)=("[^"]*"|'[^']*')/g,
          (_,key,quoted)=>key+'='+quoted[0]+phrase(quoted.slice(1,-1),true)+quoted[0])
        : phrase(part,true)) : phrase(source);
      cache.set(strings,compiled);
    }
    // No lookup on interpolation values; braces inside a value are not rescanned.
    return typeof strings === 'string' ? compiled :
      compiled.replace(slots,(_,i)=>String(values[Number(i)] ?? ''));
  }
  function tr(strings,...values) { return render(strings,values,false); }
  function ui(strings,...values) { return render(strings,values,true); }
  // Server-owned diagnostics retain their original language on disk. Translate
  // only explicit diagnostic display sites, never entire API objects or the DOM.
  let patterns;
  const diagnosticCache = new Map();
  function message(value) {
    if (locale !== 'en' || typeof value !== 'string' || !han.test(value)) return value;
    if (Object.hasOwn(catalog,value)) return catalog[value];
    if (Object.hasOwn(messages,value)) return messages[value];
    if (diagnosticCache.has(value)) return diagnosticCache.get(value);
    patterns ||= Object.entries(messages).filter(([source])=>/\{\d+\}/.test(source)).map(([source,target])=>{
      const escaped=source.split(/(\{\d+\})/).map(part=>/^\{\d+\}$/.test(part)?'([\\s\\S]*?)':part.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('');
      return [new RegExp('^'+escaped+'$'),target];
    });
    let translated = value;
    for (const [pattern,target] of patterns) {
      const found=value.match(pattern);
      if(found){translated=target.replace(slots,(_,i)=>found[Number(i)+1]);break;}
    }
    if(diagnosticCache.size>=256)diagnosticCache.delete(diagnosticCache.keys().next().value);
    diagnosticCache.set(value,translated);
    return translated;
  }
  function boot() {
    document.documentElement.lang=locale;
    // Initial document only; this runs before application scripts render data.
    const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    while(walk.nextNode())walk.currentNode.nodeValue=tr(walk.currentNode.nodeValue);
    for(const element of document.querySelectorAll('[title],[aria-label],[placeholder],[alt]'))
      for(const key of ['title','aria-label','placeholder','alt'])
        if(element.hasAttribute(key))element.setAttribute(key,tr(element.getAttribute(key)));
    document.title=tr(document.title);
  }
  return {locale,tr,ui,message,boot,missing};
})();
const {tr,ui,message:msg}=VaMPMI18n;
VaMPMI18n.boot();
