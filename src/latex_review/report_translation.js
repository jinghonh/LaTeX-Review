(function () {
  'use strict';
  const manifest = document.getElementById('translation-units');
  if (!manifest) return;
  const units = JSON.parse(manifest.textContent);
  const mode = document.getElementById('translation-mode');
  const status = document.getElementById('translation-status');
  const all = document.getElementById('translate-all');
  const cancel = document.getElementById('translation-cancel');
  const exportButton = document.getElementById('translation-export');
  let results = JSON.parse(document.getElementById('translation-results').textContent);
  const blockedStates = Object.fromEntries(units.filter(unit => unit.error)
    .map(unit => [unit.id, {state: 'blocked', message: unit.error}]));
  let states = {...blockedStates}, token = '', active = false, polling = false, available = true;
  const installed = new Set();
  const localService = location.protocol === 'http:' && location.hostname === '127.0.0.1';
  const labels = {pending: '等待翻译', running: '正在翻译', ready: '译文已完成', failed: '翻译失败，可重试', cancelled: '已停止，可重试', blocked: '此段无法翻译'};

  function applyResults() {
    Object.values(results).forEach(result => {
      if (installed.has(result.id)) return;
      const target = document.getElementById(result.side + '-' + result.node_id);
      if (!target) return;
      const block = document.createElement('div');
      block.className = 'translation-block'; block.lang = 'zh-CN';
      block.dataset.translationUnit = result.id;
      const label = document.createElement('span'); label.className = 'translation-label'; label.textContent = '中文译文';
      block.append(label);
      result.sentences.forEach(sentence => {
        const span = document.createElement('span');
        span.className = 'translated-sentence' + (sentence.changed ? ' sentence-changed' : '');
        span.dataset.originalSentence = result.side + '-' + result.node_id + '-sentence-' + sentence.number;
        // HTML is escaped by the server; protected fragments come from the existing safe preview renderer.
        span.innerHTML = sentence.html;
        block.append(span, document.createTextNode(' '));
      });
      function insert(container, translation) {
        if (result.kind === 'paragraph') {
          const paragraph = Array.from(container.children).find(child => child.tagName === 'P');
          if (paragraph) paragraph.after(translation); else container.append(translation);
        } else if (result.kind === 'figure') {
          const caption = container.querySelector('figcaption');
          if (caption) caption.after(translation); else container.append(translation);
        } else {
          const heading = Array.from(container.children).find(child => /^H[1-6]$/.test(child.tagName));
          if (heading) heading.after(translation); else container.prepend(translation);
        }
      }
      insert(target, block);
      document.querySelectorAll('[data-translation-node="' + CSS.escape(result.id) + '"]').forEach(copy => {
        const translation = block.cloneNode(true);
        if (copy.dataset.originalSentences !== undefined) {
          const ids = new Set(copy.dataset.originalSentences.split(' ').filter(Boolean));
          const sentences = Array.from(translation.querySelectorAll('.translated-sentence'))
            .filter(sentence => ids.has(sentence.dataset.originalSentence));
          translation.replaceChildren(translation.querySelector('.translation-label'));
          sentences.forEach(sentence => translation.append(sentence, document.createTextNode(' ')));
        }
        copy.append(translation);
      });
      installed.add(result.id);
    });
    document.body.classList.toggle('bilingual-view', mode.value === 'bilingual');
    renderTranslatedMath();
    window.dispatchEvent(new Event('resize'));
  }

  function renderTranslatedMath() {
    if (window.MathJax && window.MathJax.tex2chtmlPromise) {
      document.querySelectorAll('.translation-block .math-tex:not([data-translation-math])').forEach(math => {
        math.dataset.translationMath = 'true';
        if (math.querySelector('mjx-container') || math.dataset.renderError) return;
        Promise.resolve(window.MathJax.startup && window.MathJax.startup.promise)
          .then(() => window.MathJax.tex2chtmlPromise(math.textContent, {display: math.dataset.display === 'true'}))
          .then(rendered => {
            if (rendered.querySelector('mjx-merror,[data-mjx-error]')) throw new Error('公式无法排版');
            math.replaceChildren(rendered); window.dispatchEvent(new Event('resize'));
          }).catch(() => { math.textContent = '此处暂无法预览'; math.classList.add('preview-unavailable'); });
      });
    }
  }
  window.addEventListener('review-math-ready', renderTranslatedMath);

  function refreshButtons() {
    document.querySelectorAll('[data-translate-change]').forEach(button => {
      const selected = units.filter(unit => unit.change_id === button.dataset.translateChange);
      const pending = selected.some(unit => ['running', 'pending'].includes((states[unit.id] || {}).state));
      const complete = selected.every(unit => results[unit.id]);
      const blocked = selected.every(unit => unit.error);
      button.disabled = pending || blocked || (!available && !complete);
      button.textContent = blocked ? '此段无法翻译' : complete ? '显示译文' : pending ? '正在翻译…' : '翻译此处';
      const text = button.parentElement.querySelector('.translation-item-status');
      if (text) text.textContent = selected.map(unit => {
        const state = states[unit.id] || (results[unit.id] ? {state: 'ready'} : {});
        return (unit.side === 'old' ? '修改前：' : '修改后：') + (labels[state.state] || '尚未翻译') +
          (state.message ? ' · ' + state.message : '');
      }).join('；');
    });
    document.querySelectorAll('.group-translation-side[data-translation-node]').forEach(side => {
      const id = side.dataset.translationNode;
      const placeholder = side.querySelector('.translation-placeholder');
      placeholder.hidden = Boolean(results[id]);
      const state = states[id] || {};
      placeholder.textContent = labels[state.state] || '尚未翻译';
      if (state.message) placeholder.textContent += ' · ' + state.message;
    });
    cancel.disabled = !active;
    all.disabled = active || !available || !units.some(unit => !unit.error);
  }

  async function api(path, body) {
    if (!localService) throw new Error('请通过本机预览服务打开报告后继续翻译；已有译文仍可阅读。');
    const response = await fetch('/api/translation/' + path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-LaTeX-Review-Token': token},
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || '本机翻译服务请求失败');
    if (data.token) token = data.token;
    return data;
  }

  function accept(data) {
    // A served export may use different translation settings: use the active service's cache only.
    const incoming = data.enabled ? data.results || {} : results;
    installed.forEach(id => {
      if (JSON.stringify(results[id]) !== JSON.stringify(incoming[id])) {
        document.querySelectorAll('[data-translation-unit="' + CSS.escape(id) + '"]').forEach(block => block.remove());
        installed.delete(id);
      }
    });
    results = incoming; states = {...blockedStates, ...(data.states || {})};
    available = Boolean(data.enabled) && data.available !== false;
    active = Object.values(states).some(state => ['pending', 'running'].includes(state.state));
    const count = Object.keys(results).length;
    const failed = Object.values(states).filter(state => state.state === 'failed').length;
    const blocked = Object.values(blockedStates).length;
    status.textContent = data.enabled ? '译文 ' + count + ' / ' + units.length + ' 段' +
      (active ? ' · 正在处理' : '') + (failed ? ' · ' + failed + ' 段失败，可逐项重试' : '') +
      (blocked ? ' · ' + blocked + ' 段无法翻译，请查看逐项说明' : '') +
      (!available && data.message ? ' · ' + data.message : '') :
      (data.message || '请在项目配置中设置翻译接口后重新启动本机服务。');
    const usage = data.usage;
    if (data.enabled && usage && usage.requests) {
      status.textContent += usage.cache_hit_rate === null ? ' · 本次接口缓存用量未提供' :
        ' · 本次接口输入缓存 ' + (usage.cache_hit_rate * 100).toFixed(1) + '%';
      if (usage.cache_reports && usage.cache_reports < usage.requests)
        status.textContent += '（' + usage.cache_reports + '/' + usage.requests + ' 次有缓存统计）';
      if (usage.retries) status.textContent += ' · 自动重试 ' + usage.retries + ' 次';
      if (usage.message) status.textContent += ' · ' + usage.message;
    }
    applyResults(); refreshButtons();
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      do {
        accept(await api('status'));
        if (active) await new Promise(resolve => setTimeout(resolve, 600));
      } while (active);
    } catch (error) { status.textContent = error.message; active = false; refreshButtons(); }
    finally { polling = false; }
  }

  async function translate(ids) {
    mode.value = 'bilingual'; applyResults();
    const remaining = ids.filter(id => !results[id] && !blockedStates[id]);
    if (!remaining.length) return;
    try {
      const configuration = await api('status');
      accept(configuration);
      if (!available) throw new Error(configuration.message);
      accept(await api('start', {units: remaining}));
      await poll();
    } catch (error) { status.textContent = error.message; }
  }

  document.querySelectorAll('.change-card').forEach(card => {
    if (!units.some(unit => unit.change_id === card.id)) return;
    const controls = document.createElement('div'); controls.className = 'translation-item-controls';
    const button = document.createElement('button'); button.type = 'button'; button.dataset.translateChange = card.id;
    const text = document.createElement('span'); text.className = 'translation-item-status'; text.setAttribute('role', 'status');
    controls.append(button, text); card.append(controls);
    const inline = document.getElementById('inline-' + card.id);
    if (inline) {
      inline.querySelectorAll('.compact-diff .diff-group').forEach(group => {
        const jump = group.querySelector('.detail-jump');
        if (!jump) return;
        const translation = document.createElement('details'); translation.className = 'group-translation';
        const summary = document.createElement('summary'); summary.textContent = '本处中文译文';
        const groupControls = document.createElement('div'); groupControls.className = 'translation-item-controls';
        groupControls.append(button.cloneNode(true));
        translation.append(summary, groupControls);
        for (const [sideName, label] of [['old', '修改前译文'], ['new', '修改后译文']]) {
          const ids = (jump.dataset[sideName + 'Sentences'] || '').split(' ').filter(Boolean);
          const side = document.createElement('div'); side.className = 'group-translation-side';
          side.dataset.side = sideName;
          const caption = document.createElement('p'); caption.className = 'inline-heading'; caption.textContent = label;
          const placeholder = document.createElement('p'); placeholder.className = 'translation-placeholder';
          const unit = units.find(unit => unit.change_id === card.id && unit.side === sideName);
          if (ids.length && unit) {
            side.dataset.translationNode = unit.id;
            side.dataset.originalSentences = ids.map(id => sideName + '-' + id).join(' ');
            placeholder.textContent = '尚未翻译';
          } else placeholder.textContent = '该侧无对应内容';
          side.append(caption, placeholder); translation.append(side);
        }
        translation.addEventListener('toggle', () => window.dispatchEvent(new Event('resize')));
        group.append(translation);
      });
      const translation = document.createElement('details'); translation.className = 'inline-translation';
      const summary = document.createElement('summary'); summary.textContent = '整段中文译文';
      translation.append(summary, controls.cloneNode(true));
      units.filter(unit => unit.change_id === card.id).forEach(unit => {
        const side = document.createElement('div'); side.className = 'inline-translation-side';
        side.dataset.translationNode = unit.id; side.dataset.side = unit.side;
        const label = document.createElement('p'); label.className = 'inline-heading';
        label.textContent = unit.side === 'old' ? '修改前译文' : '修改后译文';
        side.append(label); translation.append(side);
      });
      translation.addEventListener('toggle', () => window.dispatchEvent(new Event('resize')));
      inline.insertBefore(translation, inline.querySelector('.inline-close'));
    }
  });
  document.addEventListener('click', event => {
    const sentence = event.target.closest('.translated-sentence.sentence-changed');
    if (sentence && !event.target.closest('a')) {
      const originalId = sentence.dataset.originalSentence;
      const side = originalId.startsWith('old-') ? 'old' : 'new';
      const id = originalId.slice(4);
      const jump = Array.from(document.querySelectorAll('.change-card .detail-jump')).find(button =>
        (button.dataset[side + 'Sentences'] || '').split(' ').includes(id));
      if (jump) {
        if (jump.closest('.change-card').hidden) {
          document.getElementById('kind-filter').value = 'all';
          document.getElementById('category-filter').value = 'all';
          document.getElementById('kind-filter').dispatchEvent(new Event('change', {bubbles: true}));
        }
        jump.click();
      }
    }
    const button = event.target.closest('[data-translate-change]');
    if (!button) return;
    event.preventDefault();
    const inline = document.getElementById('inline-' + button.dataset.translateChange);
    const translation = button.closest('.group-translation') || (inline && inline.querySelector('.inline-translation'));
    if (translation) translation.open = true;
    translate(units.filter(unit => unit.change_id === button.dataset.translateChange).map(unit => unit.id));
  });
  all.addEventListener('click', () => translate(units.map(unit => unit.id)));
  cancel.addEventListener('click', async () => {
    try { accept(await api('cancel', {})); await poll(); } catch (error) { status.textContent = error.message; }
  });
  mode.addEventListener('change', applyResults);
  exportButton.addEventListener('click', async () => {
    if (!localService) { status.textContent = '请通过本机服务导出含图片等阅读资源的译文报告。'; return; }
    try {
      const response = await fetch('/api/translation/export');
      if (!response.ok) throw new Error('导出失败，请检查报告资源是否完整。');
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'report-translated.html';
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { status.textContent = error.message; }
  });
  if (Object.keys(results).length) mode.value = 'bilingual';
  applyResults(); refreshButtons();
  if (!localService && Object.keys(results).length)
    status.textContent = '已保存 ' + Object.keys(results).length + ' / ' + units.length + ' 段译文；继续翻译需启动本机服务。';
  if (localService) poll();
})();
