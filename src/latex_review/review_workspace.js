/* Shared, capability-gated writing workspace for live and diff readers. */
(() => {
  'use strict';
  const start = () => {
    if (document.getElementById('review-workspace')) return;
    const element = (tag, text, attrs = {}) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
      return node;
    };
    const button = (text, action) => {
      const node = element('button', text, {type: 'button'});
      node.addEventListener('click', action);
      return node;
    };
    let frozen = null;
    try { frozen = JSON.parse(document.getElementById('checks-data')?.textContent || 'null'); } catch (_) { /* readable old report */ }
    let previewDiagnostics = {diagnostics: []};
    try { previewDiagnostics = JSON.parse(document.getElementById('preview-diagnostics-data')?.textContent || 'null') || previewDiagnostics; } catch (_) { /* old reports have no diagnostic adapter */ }
    const root = element('div', undefined, {id: 'review-workspace'});
    const nav = element('nav', undefined, {'aria-label': '论文工作区', role: 'tablist'});
    const reading = element('section', undefined, {id: 'workspace-reading', role: 'tabpanel', 'aria-label': '阅读与变更'});
    const preserved = Array.from(document.body.children).filter(node => !['SCRIPT', 'STYLE'].includes(node.tagName));
    preserved.forEach(node => reading.append(node));
    root.append(nav, reading);
    document.body.prepend(root);
    const names = {reading: '阅读与变更', checks: '问题检查', knowledge: '概念与符号', semantic: 'AI 审查', coverage: '规则与覆盖'};
    const panels = {reading}, tabs = {};
    Object.entries(names).forEach(([key, label]) => {
      if (key !== 'reading') {
        panels[key] = element('section', undefined, {id: `workspace-${key}`, role: 'tabpanel', 'aria-label': label, class: 'workspace-panel'});
        panels[key].append(element('h2', label));
        root.append(panels[key]);
      }
      tabs[key] = button(label, () => select(key, true));
      tabs[key].id = `workspace-tab-${key}`;
      tabs[key].setAttribute('role', 'tab');
      tabs[key].setAttribute('aria-controls', panels[key].id);
      panels[key].setAttribute('aria-labelledby', tabs[key].id);
      nav.append(tabs[key]);
    });
    // Initial selection is not a tab switch: do not save a transient parser-time
    // layout adjustment as the reading position. The reader restores persisted
    // positions after load; later switches capture the actual reading position.
    let current = null, readingY = location.hash ? window.scrollY : 0;
    function select(key, focus = false) {
      if (!panels[key]) key = 'reading';
      if (current === 'reading') readingY = window.scrollY;
      current = key;
      Object.keys(panels).forEach(name => {
        panels[name].hidden = name !== key;
        tabs[name].setAttribute('aria-selected', String(name === key));
        tabs[name].tabIndex = name === key ? 0 : -1;
      });
      // Do not rewrite the initial/default report URL or frozen-history routes.
      // Some history readers intentionally accept only their exact published URL.
      if (focus && document.querySelector('meta[name="latex-review-readonly"]')?.content !== 'snapshot') {
        try {
          const url = new URL(location.href);
          url.searchParams.set('panel', key);
          history.replaceState(null, '', url);
        } catch (_) { /* opaque historical origin */ }
      }
      if (focus) tabs[key].focus({preventScroll: true});
      window.scrollTo(0, key === 'reading' ? readingY : 0);
    }
    nav.addEventListener('keydown', event => {
      const keys = Object.keys(panels), index = keys.indexOf(current);
      const target = event.key === 'ArrowRight' ? (index + 1) % keys.length : event.key === 'ArrowLeft' ? (index + keys.length - 1) % keys.length : event.key === 'Home' ? 0 : event.key === 'End' ? keys.length - 1 : null;
      if (target !== null) { event.preventDefault(); select(keys[target], true); }
    });
    const notice = element('p', '该报告没有检查数据。', {role: 'status', class: 'workspace-status'});
    panels.checks.append(notice);
    const actions = element('div', undefined, {class: 'workspace-actions'});
    const runAll = button('检查当前论文', () => run('all'));
    const runChanged = button('检查本次修改', () => run('changed'));
    const cancel = button('取消检查', async () => { if (activeRun) await api(`runs/${activeRun}/cancel`, {}); });
    const exportButton = button('导出冻结检查结果', exportReport);
    actions.append(runAll, runChanged, cancel, exportButton);
    panels.checks.append(actions);
    const filter = element('input', undefined, {type: 'search', placeholder: '筛选规则、状态或原文', 'aria-label': '筛选检查问题'});
    panels.checks.append(filter);
    const filterFields = {};
    const filterBar = element('div', undefined, {class: 'workspace-actions'});
    Object.entries({severity: ['严重度', 'info', 'warning', 'error'], origin: ['来源', 'deterministic', 'preview_diagnostic', 'semantic'], review_state: ['人工状态', 'unreviewed', 'confirmed', 'rejected', 'waived'], lifecycle: ['生命周期', 'current', 'resolved', 'stale', 'unknown'], delta_status: ['与基线比较', 'new', 'existing', 'resolved', 'not_comparable']}).forEach(([field, values]) => {
      const select = element('select', undefined, {'aria-label': values[0]});
      select.append(element('option', values[0] + '：全部', {value: ''}));
      values.slice(1).forEach(value => select.append(element('option', value, {value})));
      select.addEventListener('change', () => { offset = 0; render(); });
      filterFields[field] = select;
      filterBar.append(select);
    });
    panels.checks.append(filterBar);
    const count = element('p', '', {role: 'status'}), list = element('ol', undefined, {class: 'workspace-findings'});
    const paging = element('div', undefined, {class: 'workspace-actions'});
    let report = frozen, findings = frozen?.findings || [], offset = 0, capability = {available: false}, activeRun = null, generation = 0;
    panels.checks.append(count, list, paging);
    const drawer = element('aside', undefined, {id: 'workspace-evidence', role: 'dialog', 'aria-modal': 'false', 'aria-label': '原文证据', tabindex: '-1'});
    drawer.hidden = true;
    root.append(drawer);
    let drawerTrigger = null;
    function closeEvidence() {
      drawer.hidden = true;
      const key = drawerTrigger?.dataset.findingKey;
      const target = drawerTrigger?.isConnected ? drawerTrigger : key ? list.querySelector(`[data-finding-key="${CSS.escape(key)}"]`) : null;
      (target || filter).focus({preventScroll: true});
    }
    drawer.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeEvidence(); } });
    let diagnosticProvenance = new Map();
    const diagnosticLocations = record => [record.diagnostic.source_new, ...(record.diagnostic.related_sources_new || [])].filter(Boolean);
    function sameLocation(location, evidence) {
      if (location.confidence !== 1 || location.uncertainty_reason || evidence.reliability !== 'exact' || location.file !== evidence.file) return false;
      const coordinates = [location.start_line, location.start_column, location.end_line, location.end_column, evidence.start_line, evidence.start_column, evidence.end_line, evidence.end_column];
      if (!coordinates.every(Number.isInteger)) return false;
      const before = (line, column, otherLine, otherColumn) => line < otherLine || (line === otherLine && column <= otherColumn);
      return before(location.start_line, location.start_column, evidence.start_line, evidence.start_column) && before(evidence.end_line, evidence.end_column, location.end_line, location.end_column);
    }
    function unifiedIssues() {
      diagnosticProvenance = new Map();
      const result = [...findings], candidates = new Map();
      const comparable = previewDiagnostics.content_id && previewDiagnostics.content_id === report?.input?.content_id;
      findings.filter(finding => finding.origin === 'deterministic' && finding.lifecycle === 'current').forEach(finding => {
        const key = JSON.stringify([finding.rule_id, finding.target]);
        candidates.set(key, [...(candidates.get(key) || []), finding]);
      });
      (previewDiagnostics.diagnostics || []).forEach(record => {
        const diagnostic = record.diagnostic, locations = diagnosticLocations(record);
        const match = comparable && record.target && (candidates.get(JSON.stringify([diagnostic.code, record.target])) || []).find(finding => locations.some(location => (finding.evidence || []).some(evidence => sameLocation(location, evidence))));
        if (match) {
          diagnosticProvenance.set(match.finding_key, [...(diagnosticProvenance.get(match.finding_key) || []), record]);
        } else {
          const key = 'preview:' + record.id;
          diagnosticProvenance.set(key, [record]);
          result.push({finding_key: key, rule_id: diagnostic.code, message: diagnostic.message,
            severity: diagnostic.severity, origin: 'preview_diagnostic', assessment: 'preview_only',
            lifecycle: comparable && !capability.stale ? 'unknown' : 'stale', review_state: 'not_applicable',
            delta_status: diagnostic.rule_status || 'not_comparable', evidence: [], related_occurrences: []});
        }
      });
      return result;
    }
    function showEvidence(finding, trigger) {
      drawerTrigger = trigger;
      drawer.replaceChildren(button('关闭证据', closeEvidence), element('h2', finding.message || finding.rule_id || finding.hypothesis));
      if (capability.stale) drawer.append(element('p', '该结果的输入已过期；下面展示冻结证据，不代表当前论文状态。'));
      ['rule_id', 'origin', 'assessment', 'severity', 'review_state', 'lifecycle', 'delta_status', 'rationale', 'missing_context'].forEach(key => {
        if (finding[key]) drawer.append(element('p', `${key}：${typeof finding[key] === 'string' ? finding[key] : JSON.stringify(finding[key])}`));
      });
      (finding.evidence || []).forEach(evidence => {
        const block = element('section');
        block.append(element('h3', `${evidence.file || '来源未知'}:${evidence.start_line || '?'} · ${evidence.include_instance || ''}`));
        if (evidence.reliability !== 'exact') block.append(element('p', `位置不可靠：${evidence.reason || '没有精确映射'}`));
        block.append(element('pre', evidence.quote || ''));
        block.append(button('复制文件与位置', async () => {
          try { await navigator.clipboard.writeText(`${evidence.file}:${evidence.start_line}:${evidence.start_column}`); } catch (_) { /* browser may disable clipboard */ }
        }));
        drawer.append(block);
      });
      const diagnostics = diagnosticProvenance.get(finding.finding_key) || [];
      if (diagnostics.length) {
        drawer.append(element('h3', '预览诊断来源（仅展示，不是检查源码证据）'));
        diagnostics.forEach(record => drawer.append(element('pre', JSON.stringify(record.diagnostic, null, 2))));
        if (finding.origin === 'preview_diagnostic') drawer.append(element('p', '该诊断没有独立验证的源码证据，不提供作者决定或源码批注操作。'));
      }
      if (capability.available && capability.capabilities?.decisions && !capability.stale && report?.input_id === capability.current_input_id && finding.origin === 'deterministic' && finding.lifecycle === 'current') {
        const decisions = element('div', undefined, {class: 'workspace-actions'});
        [['confirm', '作者确认问题'], ['reject', '拒绝此建议'], ['waive', '添加有条件例外'], ['defer', '暂不决定']].forEach(([action, label]) => {
          const control = button(label, async () => {
            const reason = window.prompt(`${label}的理由（不会修改 LaTeX 源文件）`);
            if (!reason?.trim()) return;
            control.disabled = true;
            try {
              if (!crypto.subtle) throw new Error('浏览器不支持本地证据校验，请使用 localhost 安全上下文。');
              const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(finding.evidence)));
              const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
              const knowledgeSource = action === 'waive' ? (await api('knowledge')).source : undefined;
              const response = await api(`findings/${encodeURIComponent(finding.finding_key)}/decisions`, {
                input_id: report.input_id, request_id: crypto.randomUUID(), action, reason: reason.trim(),
                evidence_fingerprint: fingerprint, scope: {id: 'project', domain: 'project'}, author: 'author',
                ...(knowledgeSource ? {expected_source: knowledgeSource} : {})});
              Object.assign(finding, response.finding);
              capability = await api('status');
              render();
              showEvidence(finding, list.querySelector(`[data-finding-key="${CSS.escape(finding.finding_key)}"]`) || trigger);
            } catch (error) { drawer.append(element('p', error.message, {role: 'alert'})); control.disabled = false; }
          });
          decisions.append(control);
        });
        drawer.append(decisions);
      }
      if (capability.available && capability.capabilities?.annotation_conversion && !capability.stale && report?.input_id === capability.current_input_id && finding.origin === 'deterministic' && finding.lifecycle === 'current' && finding.evidence?.length && finding.evidence.every(e => e.reliability === 'exact')) {
        const convert = button('转为批注（不解决问题）', async () => {
          if (!window.confirm('将此问题转入现有批注？不会修改 LaTeX 源文件，也不会将问题标为已解决。重复操作会打开同一批注。')) return;
          convert.disabled = true;
          try {
            const annotations = window.reviewAnnotations;
            if (!annotations?.isReady() || !annotations.refresh || !annotations.focus) throw new Error('当前页面的批注接口不可用，请重建或刷新预览。');
            if (!crypto.subtle) throw new Error('浏览器不支持本地证据校验，请使用 localhost 安全上下文。');
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(finding.evidence)));
            const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
            const linked = await api(`findings/${encodeURIComponent(finding.finding_key)}/annotation`, {input_id: report.input_id, evidence_fingerprint: fingerprint});
            await annotations.refresh();
            const thread = annotations.getSnapshot().threads.find(item => item.id === linked.annotation_thread_id);
            if (!thread) throw new Error('该问题已关联批注，但批注已删除或当前页面无法显示；不会重复新建。');
            select('reading');
            drawer.hidden = true;
            annotations.focus(thread);
          } catch (error) { drawer.append(element('p', error.message, {role: 'alert'})); }
          finally { convert.disabled = false; }
        });
        drawer.append(convert);
      }
      const occurrence = (finding.related_occurrences || []).find(item => item.node_id);
      if (occurrence) drawer.append(button('在论文中查看', () => {
        select('reading');
        const node = document.getElementById(occurrence.node_id) || document.querySelector(`[data-node-id="${CSS.escape(occurrence.node_id)}"]`);
        if (node) { node.scrollIntoView({block: 'center'}); readingY = window.scrollY; }
        else drawer.append(element('p', '当前阅读页无可靠展示锚点，请查看上方原始 LaTeX。'));
      }));
      drawer.hidden = false;
      drawer.focus({preventScroll: true});
    }
    function render() {
      const issues = unifiedIssues();
      const selected = issues.filter(item => (!filter.value || JSON.stringify([item, diagnosticProvenance.get(item.finding_key)]).toLocaleLowerCase().includes(filter.value.toLocaleLowerCase())) && Object.entries(filterFields).every(([field, select]) => !select.value || (field === 'origin' && select.value === 'preview_diagnostic' ? diagnosticProvenance.has(item.finding_key) : (field === 'lifecycle' && capability.stale ? 'stale' : item[field]) === select.value)));
      if (offset >= selected.length) offset = 0;
      const hits = item => item.origin === 'preview_diagnostic' ? new Set((diagnosticProvenance.get(item.finding_key) || []).flatMap(diagnosticLocations).filter(location => location.file && location.start_line).map(location => JSON.stringify([location.file, location.start_line, location.start_column, location.end_line, location.end_column]))).size : (item.evidence?.length || 0);
      count.textContent = `问题条目数 ${selected.length} · 命中位置数 ${selected.reduce((sum, item) => sum + hits(item), 0)}`;
      list.replaceChildren();
      selected.slice(offset, offset + 50).forEach(finding => {
        const row = element('li');
        const open = button(`${finding.message || finding.rule_id} · ${finding.lifecycle} · ${finding.review_state}`, () => showEvidence(finding, open));
        open.dataset.findingKey = finding.finding_key;
        const source = finding.origin === 'preview_diagnostic' ? '预览诊断（仅展示）' : finding.origin === 'semantic' ? 'AI 候选' : '确定性规则';
        row.append(open, element('span', ` · 来源：${source}${finding.origin !== 'preview_diagnostic' && diagnosticProvenance.has(finding.finding_key) ? ' + 预览诊断' : ''}`));
        list.append(row);
      });
      paging.replaceChildren();
      if (offset) paging.append(button('上一页', () => { offset -= 50; render(); }));
      if (offset + 50 < selected.length) paging.append(button('下一页', () => { offset += 50; render(); }));
      const coverage = panels.coverage;
      coverage.replaceChildren(element('h2', names.coverage));
      if (!report) coverage.append(element('p', '该报告没有检查数据。'));
      else {
        coverage.append(element('p', '覆盖描述适用范围，不代表论文正确率；AI 未运行不等于语义检查通过。'));
        (report.coverage || []).forEach(item => {
          const section = element('section');
          section.append(element('h3', `${item.rule_id} · ${item.status}`), element('p', (item.reasons || []).join('；') || '适用范围见规则前提'));
          (item.blind_spots || []).forEach(spot => section.append(element('p', `${spot.code}：${spot.message}${spot.file ? ' · ' + spot.file : ''}`)));
          coverage.append(section);
        });
      }
      if (report) {
        const incomplete = (report.coverage || []).some(item => !item.complete);
        notice.textContent = `${capability.stale ? '上次结果（已过期） · ' : ''}${issues.length ? '检查与预览问题' : '已检查范围内未发现规则问题'}${incomplete ? ' · 部分范围未检查' : ''}`;
      }
      runAll.disabled = runChanged.disabled = !capability.available || !capability.capabilities?.run || !!activeRun;
      cancel.disabled = !activeRun;
      exportButton.disabled = !report;
      window.dispatchEvent(new CustomEvent('review-checks-updated', {detail: {report, capability}}));
    }
    filter.addEventListener('input', () => { offset = 0; render(); });
    async function api(path, body) {
      const response = await fetch('api/checks/' + path, {
        method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', ...(capability.token ? {'X-LaTeX-Review-Token': capability.token} : {}), ...(capability.paper_id ? {'X-LaTeX-Review-Paper-ID': capability.paper_id} : {}), 'X-LaTeX-Review-Page-Version': document.querySelector('meta[name="latex-review-annotation-version"]')?.content || capability.page_version || '', 'X-LaTeX-Review-Checks-Content-ID': frozen?.input?.content_id || ''},
        ...(body === undefined ? {} : {body: JSON.stringify(body)})
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.message || value.error?.message || `请求失败 (${response.status})`);
      return value;
    }
    async function loadFindings(runId) {
      let cursor = 0, result = [];
      do {
        const value = await api(`findings?limit=50&cursor=${cursor}${runId ? '&run_id=' + encodeURIComponent(runId) : ''}`);
        result.push(...value.findings);
        if (value.next_cursor === null || value.next_cursor === undefined) break;
        cursor = value.next_cursor;
      } while (true);
      findings = result;
      try { report = await api(`export${runId ? '?run_id=' + encodeURIComponent(runId) : ''}`); } catch (_) { report = {...report, findings}; }
      capability = {...capability, ...await api('status')};
      render();
    }
    async function run(scope) {
      const epoch = ++generation;
      try {
        const task = await api('runs', {scope});
        activeRun = task.run_id;
        render();
        notice.textContent = '正在检查当前论文；旧结果仅供参考。';
        const poll = async () => {
          if (epoch !== generation) return;
          try {
            const state = await api(`runs/${activeRun}`);
            notice.textContent = `检查状态：${state.state || state.status}`;
            if (['queued', 'running', 'pending', 'cancelling'].includes(state.state || state.status)) { setTimeout(poll, 350); return; }
            const completed = activeRun;
            activeRun = null;
            capability = await api('status');
            if (['complete', 'completed', 'partial', 'succeeded'].includes(state.state || state.status)) await loadFindings(completed);
            render();
          } catch (error) { activeRun = null; render(); notice.textContent = error.message; }
        };
        poll();
      } catch (error) { activeRun = null; render(); notice.textContent = error.message; }
    }
    function exportReport() {
      const includeEvidence = window.confirm('导出包含原文证据？取消则只导出摘要与覆盖。');
      const exported = JSON.parse(JSON.stringify(report));
      const hasAI = exported.findings?.some(item => item.origin === 'semantic') || exported.semantic_candidates?.length;
      if (hasAI && !window.confirm('导出中包含 AI 候选？取消则仅导出确定性检查。')) {
        exported.findings = (exported.findings || []).filter(item => item.origin !== 'semantic');
        delete exported.semantic_candidates;
      }
      if (!includeEvidence) (exported.findings || []).forEach(item => { item.evidence = []; item.related_occurrences = []; });
      const blob = new Blob([JSON.stringify(exported, null, 2)], {type: 'application/json'});
      const link = element('a', 'checks.json', {download: 'checks.json', href: URL.createObjectURL(blob)});
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }
    panels.knowledge.append(element('p', '手工登记概念与符号不需要 AI；只有活动本地服务可以修改知识。'));
    panels.semantic.append(element('p', 'AI 审查未运行，不表示语义检查通过。未配置接口时可导入与本地请求匹配的候选。'));
    window.addEventListener('review-knowledge-updated', async () => {
      try { capability = await api('status'); render(); } catch (error) { notice.textContent = error.message; }
    });
    window.reviewWorkspace = {element, button, panels, api, showEvidence, get capability() { return capability; }, get report() { return report; }, get unifiedIssues() { return unifiedIssues(); }, refresh: render};
    select(new URLSearchParams(location.search).get('panel') || 'reading');
    render();
    window.dispatchEvent(new Event('review-workspace-ready'));
    if (location.protocol === 'http:' || location.protocol === 'https:') {
      api('status').then(async value => {
        capability = value;
        if (!value.available) notice.textContent = value.message || '当前页面只能阅读冻结检查结果，运行检查需要本地入口服务。';
        else if (value.current_run) await loadFindings(typeof value.current_run === 'string' ? value.current_run : value.current_run.run_id);
        else if (!report) notice.textContent = '尚未检查当前版本';
        render();
        window.dispatchEvent(new Event('review-workspace-capabilities'));
      }).catch(() => { render(); if (!report) notice.textContent = '该报告没有检查数据；静态或历史页面不提供写操作。'; });
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
