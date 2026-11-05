/* Candidate-only semantic tasks. Never send automatically on save or page load. */
(() => {
  'use strict';
  let initialized = false;
  function start() {
    const workspace = window.reviewWorkspace;
    if (!workspace || initialized) return;
    initialized = true;
    const {element: el, button, api} = workspace;
    const host = el('div', undefined, {id: 'semantic-manager'});
    workspace.panels.semantic.append(host);
    let task = null, busy = false, message = '', timer = null;
    let selectedTask = 'concept_definition', selectionMode = 'all', units = [], unitInputId = null, nextCursor = null, unitTotal = 0;
    const selectedUnits = new Set();
    async function loadUnits(cursor = 0) {
      const data = await api(`knowledge/evidence?limit=100&cursor=${cursor}`);
      if (workspace.report?.input_id && data.input_id !== workspace.report.input_id) throw new Error('证据与当前检查输入不符，请先复检并刷新。');
      if (unitInputId && unitInputId !== data.input_id) selectedUnits.clear();
      unitInputId = data.input_id;
      units = data.units.map((unit, offset) => ({...unit, index: Number(cursor) + offset}));
      nextCursor = data.next_cursor; unitTotal = data.total; selectionMode = 'selected';
    }
    const allowed = () => workspace.capability.available && workspace.capability.capabilities?.semantic_import;
    const pending = () => task && ['queued', 'running', 'importing', 'cancelling'].includes(task.state);
    const uuid = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const storageKey = () => `latex-review-semantic-${workspace.capability.paper_id}`;
    function remember() { try { sessionStorage.setItem(storageKey(), task.request_id); } catch (_) { /* optional local convenience */ } }
    async function action(fn) {
      if (!allowed() || busy) return;
      busy = true; message = ''; render();
      try { await fn(); } catch (error) { message = error.message; }
      finally { busy = false; render(); }
    }
    function schedule() {
      clearTimeout(timer);
      if (pending() && allowed()) timer = setTimeout(async () => {
        try { task = await api(`semantic/tasks/${encodeURIComponent(task.request_id)}`); render(); schedule(); }
        catch (error) { message = error.message; render(); }
      }, 650);
    }
    function download(name, value) {
      const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}));
      const link = el('a', name, {href: url, download: name});
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    async function decide(candidate, kind) {
      const reason = window.prompt(kind === 'reject' ? '拒绝原因（同输入与相同证据不重复推荐）' : kind === 'confirm_issue' ? '作者确认潜在问题的理由（不等同模型证明）' : '暂不决定的原因');
      if (!reason?.trim()) return;
      await action(async () => {
        task = await api('semantic/decisions', {request_id: task.request_id, candidate_id: candidate.candidate_id,
          input_id: task.input_id, action: kind, reason: reason.trim(), decision_id: uuid()});
      });
    }
    function render() {
      host.replaceChildren();
      host.append(el('p', '语义审查尚未证明论文正确。所有输出均为待审候选；证据校验只证明原文来源，不证明假设真实。'));
      if (!allowed()) {
        host.append(el('p', '静态、历史或未授权页面只读；创建请求、导入、调用和候选决定均不可用。'));
        clearTimeout(timer); return;
      }
      const form = el('form'), select = el('select', undefined, {'aria-label': '语义任务'});
      [['concept_definition', '发现概念与定义'], ['name_mapping', '名称与代码映射'], ['cross_section_comparison', '跨章节潜在矛盾']].forEach(([value, label]) => select.append(el('option', label, {value})));
      select.value = selectedTask;
      select.addEventListener('change', () => { selectedTask = select.value; });
      const create = button('准备本地请求（不联网）', () => {
        const selected = select.value;
        action(async () => {
          if (selectionMode === 'selected' && !selectedUnits.size) throw new Error('请选择至少一个片段；跨章节比较需选择至少两个章节。');
          const selection = selectionMode === 'selected' ? {unit_indexes: [...selectedUnits].sort((a, b) => a - b), input_id: unitInputId} : {};
          task = await api('semantic/tasks', {task: selected, mode: 'export', ...selection});
          remember(); schedule();
        });
      });
      create.disabled = busy || pending();
      const scope = el('select', undefined, {'aria-label': '语义审查范围'});
      [['all', '当前入口全部可靠片段（明确全文）'], ['selected', '仅手动选中的片段']].forEach(([value, label]) => scope.append(el('option', label, {value})));
      scope.value = selectionMode;
      scope.addEventListener('change', () => { selectionMode = scope.value; render(); });
      form.append(el('label', '请选择任务及发送范围：'), select, scope, create);
      const load = button('加载当前原文片段以缩小范围', () => action(() => loadUnits()));
      load.disabled = busy || pending();
      form.append(load);
      if (selectionMode === 'selected') {
        form.append(el('p', `已选 ${selectedUnits.size} 个片段（最多 512）；当前页 ${units.length} / 总计 ${unitTotal}。需先运行当前检查加载可靠证据；换页保留选择。`));
        for (const unit of units) {
          const label = el('label'), checkbox = el('input', undefined, {type: 'checkbox', 'aria-label': `选择片段 ${unit.index}`});
          checkbox.checked = selectedUnits.has(unit.index);
          checkbox.disabled = !unit.evidence?.length || unit.evidence.some(e => e.reliability !== 'exact');
          checkbox.addEventListener('change', () => {
            if (checkbox.checked && selectedUnits.size >= 512) { checkbox.checked = false; return; }
            if (checkbox.checked) selectedUnits.add(unit.index); else selectedUnits.delete(unit.index);
            const counter = host.querySelector('[data-semantic-selected]'); if (counter) counter.textContent = `当前已选 ${selectedUnits.size} 个片段`;
          });
          label.append(checkbox, document.createTextNode(`${unit.index} · ${(unit.section_path || []).join(' / ')} · ${(unit.evidence || []).map(e => e.quote).join(' ').slice(0, 500)}`));
          form.append(label, el('br'));
        }
        form.append(el('p', `当前已选 ${selectedUnits.size} 个片段`, {'data-semantic-selected': ''}));
        if (nextCursor !== null) form.append(button('下一页原文片段', () => action(() => loadUnits(nextCursor))));
        form.append(button('清空片段选择', () => { selectedUnits.clear(); render(); }));
      }
      form.addEventListener('submit', event => event.preventDefault());
      host.append(form, el('p', message || (task ? `请求 ${task.request_id} · 状态 ${task.state}` : '未运行语义审查；不等于零问题。'), {role: 'status'}));
      if (!task) return;
      if (task.error) host.append(el('pre', JSON.stringify(task.error, null, 2)));
      if (task.preview) host.append(el('p', `发送预览：入口 ${task.preview.entry}；${task.preview.unit_count} 个原始片段；章节 ${task.preview.sections.join(' / ')}。导出包含原文，请自行确认分享范围。`));
      const actions = el('div');
      const exportButton = button('导出请求 JSON', () => action(async () => download('semantic-request.json', await api(`semantic/tasks/${encodeURIComponent(task.request_id)}/export`))));
      exportButton.disabled = busy || !task.preview;
      const invoke = button('手动调用已配置模型', () => {
        if (!window.confirm(`将发送以上原文到显式配置模型 ${task.model}（${task.provider || 'openai-compatible'}，thinking=${task.thinking || 'default'}）。预算：${JSON.stringify(task.budget)}。确认本次调用？`)) return;
        action(async () => { task = await api(`semantic/tasks/${encodeURIComponent(task.request_id)}/invoke`, {}); schedule(); });
      });
      invoke.disabled = busy || pending() || !task.preview || !task.model_configured || task.called;
      const cancel = button('取消任务', () => action(async () => { task = await api(`semantic/tasks/${encodeURIComponent(task.request_id)}/cancel`, {}); schedule(); }));
      cancel.disabled = busy || !pending();
      actions.append(exportButton, invoke, cancel);
      host.append(actions, el('p', task.model_configured ? `配置模型：${task.model}；提供商：${task.provider || 'openai-compatible'}；思考模式：${task.thinking || 'default'}；每任务预算 ${JSON.stringify(task.budget)}；不会在保存时自动调用。` : '未显式配置模型：仍可导出请求，使用固定响应或外部审阅后导入；不尝试发现任何凭据。'));
      const importForm = el('form'), field = el('textarea', undefined, {'aria-label': '模型响应 JSON', rows: '8'});
      const file = el('input', undefined, {type: 'file', accept: '.json,application/json', 'aria-label': '选择响应 JSON 文件'});
      file.addEventListener('change', async () => {
        const selected = file.files[0]; if (!selected) return;
        if (selected.size > 524288) { message = '响应超过 512 KiB 上限'; render(); return; }
        field.value = await selected.text();
      });
      const importButton = button('验证并导入响应（仅匹配此本地请求）', () => {
        const response = field.value;
        if (new TextEncoder().encode(response).length > 524288) { message = '响应超过 512 KiB 上限'; render(); return; }
        action(async () => { task = await api('semantic/import', {request_id: task.request_id, response}); schedule(); });
      });
      importButton.disabled = busy || pending() || !task.preview;
      importForm.addEventListener('submit', event => event.preventDefault());
      importForm.append(el('p', '只导入响应，不导入请求作为信任依据。重复引文必须携带精确范围；陈旧版本保留历史但不能确认。'), file, field, importButton);
      host.append(importForm);
      if (!task.result) return;
      const result = task.result, candidates = result.candidates || [];
      host.append(el('p', `验证状态 ${result.status}；有效 ${result.valid_count ?? candidates.length}；无效 ${result.invalid_count ?? 0}；陈旧 ${result.stale_count ?? 0}；待审 ${candidates.filter(c => c.review_state === 'unreviewed' && c.lifecycle === 'current').length}。`));
      (result.errors || []).filter(Boolean).forEach(error => host.append(el('p', `${error.code}：${error.message}`)));
      for (const candidate of candidates) {
        const row = el('article', undefined, {class: 'semantic-candidate'});
        row.append(el('h3', `${candidate.kind} · ${candidate.review_state || 'unreviewed'} · ${candidate.lifecycle}`), el('p', candidate.hypothesis), el('p', candidate.rationale));
        if (candidate.missing_context?.length) row.append(el('p', `尚缺上下文：${candidate.missing_context.join('；')}`));
        const evidence = button('查看精确原文证据', () => workspace.showEvidence({...candidate, evidence: candidate.sources}, evidence));
        row.append(evidence);
        const current = candidate.lifecycle === 'current' && result.status !== 'stale' && (!workspace.report?.input_id || workspace.report.input_id === task.input_id);
        const reject = button('拒绝候选', () => decide(candidate, 'reject'));
        const defer = button('暂不决定', () => decide(candidate, 'defer'));
        reject.disabled = defer.disabled = busy || !current;
        row.append(reject, defer);
        if (['potential_contradiction', 'missing_qualification'].includes(candidate.kind)) {
          const confirm = button('作者确认问题（非知识关系）', () => decide(candidate, 'confirm_issue'));
          confirm.disabled = busy || !current; row.append(confirm);
        } else {
          const relation = button('到知识面板显式确认关系', () => {
            if (!window.confirm('这不会自动确认。请在知识面板选择已有对象、校验证据并再次提交作者决定。')) return;
            window.dispatchEvent(new CustomEvent('review-semantic-confirm-relation', {detail: {candidate, input_id: task.input_id, request_id: task.request_id}}));
            document.getElementById('workspace-tab-knowledge')?.click();
          });
          relation.disabled = busy || !current || !workspace.capability.capabilities?.knowledge; row.append(relation);
        }
        host.append(row);
      }
    }
    window.addEventListener('review-workspace-capabilities', async () => {
      render();
      if (!allowed() || task) return;
      let known;
      try { known = sessionStorage.getItem(storageKey()); } catch (_) { /* optional */ }
      if (known) { try { task = await api(`semantic/tasks/${encodeURIComponent(known)}`); render(); schedule(); } catch (_) { /* unknown/stale host request is not imported authority */ } }
    });
    window.addEventListener('review-checks-updated', render);
    render();
  }
  window.addEventListener('review-workspace-ready', start);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
