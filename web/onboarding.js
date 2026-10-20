'use strict';
const firstRun={
  stage:'done',busy:false,
  start(status){
    this.stage=status.onboarding?.stage||'done';
    if(this.stage!=='done')this.show();
  },
  show(){
    const language=ui`<label class="form-row"><span>语言 / Language</span><select id="onboarding-language"><option value="zh-CN" ${VaMPMI18n.locale==='zh-CN'?'selected':''}>简体中文</option><option value="en" ${VaMPMI18n.locale==='en'?'selected':''}>English</option></select></label>`;
    const error='<p id="onboarding-error" class="notice error" role="alert" hidden></p>';
    if(this.stage==='game'){
      openModal(ui`<div id="onboarding"><h2>欢迎使用 VaM 包管家</h2><p>第 1 步：选择游戏目录</p><p class="spaced">程序不会自动把解压位置当作游戏目录。请选择已有的 VaM 根目录，其中应包含 AddonPackages。</p>${language}<form id="onboarding-directory-form">${directoryPathField()}<label class="form-row"><span>目录名称</span><input name="name" required maxlength="80" placeholder="选择游戏目录后自动填写"><small data-directory-name-error role="alert" hidden></small></label><p>确认后仅在所选游戏目录创建管理文件夹，不扫描或移动资源。可跳过，稍后到设置添加。</p>${error}<div class="modal-actions"><button type="button" class="button" data-onboarding-action="skip_game">暂不选择目录</button><button type="submit" class="button primary">保存目录并继续</button></div></form></div>`);
      validateDirectoryForm($('#onboarding-directory-form'));
    }else{
      openModal(ui`<div id="onboarding"><h2>设置 AI 助手（可选）</h2><p>第 2 步：配置 AI 服务</p><p class="spaced">可选择 Codex、DeepSeek 或其他兼容 API，设置模型与自己的账号或密钥。AI 不是管理资源的必需条件。</p>${language}<p>配置入口使用完整的 AI 设置页面；也可以跳过，之后随时在“AI 与设置”中配置。</p>${error}<div class="modal-actions"><button type="button" class="button" data-onboarding-action="finish">稍后配置，进入主页</button><button type="button" class="button primary" data-onboarding-action="configure_ai">前往 AI 设置</button></div></div>`);
    }
  },
  async act(action,data={}){
    if(this.busy)return;
    this.busy=true;
    const buttons=[...$('#onboarding').querySelectorAll('button,select')];
    const previous=buttons.map(button=>button.disabled);
    buttons.forEach(button=>button.disabled=true);
    try{
      const result=await api('onboarding',{action:action==='configure_ai'?'finish':action,...data});
      this.stage=result.stage;
      if(action==='select_game'){reloadWorkspace();return;}
      if(this.stage==='done'){
        closeModal();
        if(action==='configure_ai'){
          await changePage('settings');
          $('#settings-form')?.scrollIntoView({block:'start'});
          $('#ai-provider')?.focus({preventScroll:true});
        }
      }else this.show();
    }catch(error){
      const notice=$('#onboarding-error');
      if(notice){notice.textContent=error.message;notice.hidden=false;}else toast(error.message,true);
    }finally{
      this.busy=false;
      buttons.forEach((button,i)=>{if(button.isConnected)button.disabled=previous[i];});
    }
  }
};
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-onboarding-action]');
  if(!button)return;
  event.preventDefault();event.stopImmediatePropagation();
  firstRun.act(button.dataset.onboardingAction);
});
document.addEventListener('input',event=>{
  if(event.target.closest('#onboarding-directory-form')){
    const notice=$('#onboarding-error');if(notice)notice.hidden=true;
  }
});
document.addEventListener('submit',event=>{
  if(event.target.id!=='onboarding-directory-form')return;
  event.preventDefault();event.stopImmediatePropagation();
  if(validateDirectoryForm(event.target))firstRun.act('select_game',Object.fromEntries(new FormData(event.target)));
});
document.addEventListener('change',async event=>{
  if(event.target.id!=='onboarding-language')return;
  event.target.disabled=true;
  try{await api('language',{language:event.target.value});reloadWorkspace();}
  catch(error){event.target.disabled=false;$('#onboarding-error').textContent=error.message;$('#onboarding-error').hidden=false;}
});
document.addEventListener('DOMContentLoaded',()=>{
  $('#dialog').addEventListener('cancel',event=>{
    if(!$('#onboarding'))return;
    event.preventDefault();
    firstRun.act('finish');
  });
});
