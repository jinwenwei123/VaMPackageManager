/* Copy choices are previewed separately from executing file transfers. */
window.VaMPMActivation = (() => {
  function render(plan, {esc, bytes, areas}) {
    if (plan.kind !== 'activate') return '';
    const all = plan.duplicate_mode === 'all';
    const groups = plan.copy_conflicts || [];
    return ui`<section class="activation-controls" aria-label="同名副本设置">
      <h3>同名副本</h3>
      <div class="copy-modes">
        <label><input type="radio" name="copy-mode" value="select" ${all?'':'checked'}><span><strong>指定副本</strong><small>选择一份使用，其余放入停用库</small></span></label>
        <label><input type="radio" name="copy-mode" value="all" ${all?'checked':''}><span><strong>全部保留</strong><small>保持文件名，放在不同子文件夹</small></span></label>
      </div>
      <p class="copy-hint">全部保留时，VaM 对同一包名及版本只采用一份，不会合并内容，也不保证采用顺序。依赖会汇总所有可读取副本的声明。</p>
      ${groups.map(group => ui`<div class="copy-group" data-copy-group="${esc(group.key)}">
        <div class="copy-heading"><strong>${esc(group.pkgid)}</strong><span class="badge ${group.unresolved?'orange':''}">${group.variants.length} 种内容 · ${group.copies} 份文件</span></div>
        ${group.stale&&!all?ui('<p class="copy-stale">记住的副本已不可用，请重新选择。</p>'):''}
        <label class="form-row"><span data-copy-selection-label ${all?'hidden':''}>使用的副本${group.unresolved?tr(' · 待选择'):''}</span>
          <span data-copy-all ${all?'':'hidden'}>此组将保留全部 ${group.copies} 份文件</span>
          <select data-copy-key="${esc(group.key)}" aria-label="${esc(group.pkgid)} 使用的副本" ${all?'disabled hidden':''}>
            <option value="">${all?tr('全部副本均保留'):tr('请选择一份可读取的副本')}</option>
            ${group.variants.map((v,n) => `<option value="${esc(v.sha256)}" ${v.valid&&v.sha256===(group.selected||plan.copy_choices[group.key])?'selected':''} ${v.valid?'':'disabled'}>${n+1}. ${bytes(v.size)} · ${v.valid?tr('可读取'):tr('不可读取')} · ${esc(areas[v.files[0].area])} / ${esc(v.files[0].rel)}${v.copies>1?'（'+v.copies+tr(' 份相同内容）'):''}</option>`).join('')}
          </select>
        </label>
        <details class="copy-details"><summary>查看路径与内容指纹</summary>
          ${group.variants.map((v,n) => ui`<div class="copy-variant"><strong>副本 ${n+1} · ${bytes(v.size)} · ${v.valid?tr('可读取'):tr('不可读取')}</strong><small class="mono" title="SHA-256">${esc(v.sha256)}</small>${v.error?`<p class="copy-stale">${esc(msg(v.error))}</p>`:''}${v.files.map(f=>`<div class="copy-path">${esc(areas[f.area])} / ${esc(f.rel)}</div>`).join('')}</div>`).join('')}
        </details>
      </div>`).join('')}
      ${!groups.length?ui('<p class="copy-hint">当前包含的包没有同名副本。</p>'):''}
      <label class="checkline"><input type="checkbox" id="remember-copies" ${plan.remember_copies?'checked':''}>${plan.profile_id?tr('记住此启动方案的选择'):tr('记住临时选择的偏好')}</label>
      <p class="copy-hint">勾选后，点击“更新预览”即保存选择；确认执行才移动文件。相同内容会自动选一份，不可读取的副本不能单独选用。</p>
      <div id="copy-preview-status" class="copy-preview-status" role="status">${plan.choices_saved?tr('选择已记住。'):plan.unresolved_copies?.length?tr('请先完成副本选择，再更新预览。'):tr('可以调整副本选择，再更新预览。')}</div>
    </section>`;
  }

  function changed(target, dialog) {
    if (!target.closest('.activation-controls')) return false;
    const all = dialog.querySelector('[name="copy-mode"]:checked').value === 'all';
    dialog.querySelectorAll('[data-copy-key]').forEach(el => {el.disabled = all;el.hidden = all;});
    dialog.querySelectorAll('[data-copy-selection-label]').forEach(el => {el.hidden = all;});
    dialog.querySelectorAll('[data-copy-all]').forEach(el => {el.hidden = !all;});
    dialog.querySelector('[data-action="apply-plan"]').disabled = true;
    dialog.querySelector('#copy-preview-status').textContent = tr('选择已变更，请更新预览。');
    return true;
  }

  function request(plan, dialog) {
    const choices = {...plan.copy_choices};
    const mode = dialog.querySelector('[name="copy-mode"]:checked').value;
    dialog.querySelectorAll('[data-copy-key]').forEach(el => {
      if (el.value) choices[el.dataset.copyKey] = el.value;
      else if (mode === 'select') delete choices[el.dataset.copyKey];
    });
    return {...plan.activation_request,
      duplicate_mode:mode,
      copy_choices:choices, remember_copies:dialog.querySelector('#remember-copies').checked};
  }
  return {render, changed, request};
})();
