'use strict';
// Runs before CSS/first paint. Preferences come from the app, not port-scoped storage.
window.VaMPMTheme = (() => {
  const palettes = [['forest',tr('森林绿')],['ocean',tr('海洋蓝')],['violet',tr('鸢尾紫')],
    ['rose',tr('玫瑰粉')],['amber',tr('琥珀橙')],['graphite',tr('石墨灰')]];
  const modes = [['light',tr('浅色')],['dark',tr('深色')],['system',tr('跟随系统')]];
  const valid = value => ({palette:palettes.some(p=>p[0]===value?.palette)?value.palette:'forest',
    mode:modes.some(m=>m[0]===value?.mode)?value.mode:'light'});
  const system = matchMedia('(prefers-color-scheme: dark)');
  let settings = valid(window.VaMPMAppearanceConfig), saved = {...settings};
  let pending = 0, revision = 0, sequence = Promise.resolve();
  let iconColors='',iconUrl='';
  function paintIcon(){
    const source=document.querySelector('.brand-icon'),link=document.querySelector('link[rel=icon]');
    if(!source||!link)return;
    const styles=getComputedStyle(document.documentElement),accent=styles.getPropertyValue('--green').trim(),ink=styles.getPropertyValue('--on-primary').trim();
    if(!accent||!ink||iconColors===accent+ink)return;
    const svg=source.cloneNode(true);svg.removeAttribute('class');svg.removeAttribute('aria-hidden');
    svg.querySelector('rect').setAttribute('fill',accent);
    svg.querySelectorAll('path').forEach(p=>p.setAttribute('stroke',ink));
    const previous=iconUrl;iconColors=accent+ink;
    iconUrl=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],{type:'image/svg+xml'}));
    link.href=iconUrl;if(previous)URL.revokeObjectURL(previous);
  }
  function paint(){
    const dark = settings.mode==='dark'||settings.mode==='system'&&system.matches;
    document.documentElement.dataset.palette = settings.palette;
    document.documentElement.dataset.mode = dark?'dark':'light';
    for(const button of document.querySelectorAll('[data-palette-choice]'))
      button.setAttribute('aria-pressed',String(button.dataset.paletteChoice===settings.palette));
    for(const button of document.querySelectorAll('[data-mode-choice]'))
      button.setAttribute('aria-pressed',String(button.dataset.modeChoice===settings.mode));
    const toggle = document.querySelector('[data-action="toggle-theme"]');
    if(toggle){toggle.textContent=dark?'☀':'☾';toggle.title=toggle.ariaLabel=dark?tr('切换浅色模式'):tr('切换深色模式');}
    const status = document.querySelector('#appearance-status');
    if(status)status.textContent=pending?tr('正在保存…'):tr('已自动保存');
    paintIcon();
  }
  async function change(patch, save){
    const next = valid({...settings,...patch});
    settings=next;pending++;revision++;paint();
    const operation=sequence.catch(()=>{}).then(()=>save(next));
    sequence=operation;
    try{saved=valid(await operation);}
    finally{pending--;if(!pending)settings={...saved};paint();}
  }
  function sync(value, before){
    if(!value||pending||before!==revision)return;
    const next=valid(value);
    if(next.mode!==settings.mode||next.palette!==settings.palette){settings=next;saved={...next};paint();}
  }
  system.addEventListener('change',paint);
  paint();
  return {palettes,modes,paint,change,sync,current:()=>({...settings}),revision:()=>revision};
})();
