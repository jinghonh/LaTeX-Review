(function () {
  'use strict';
  const readonly = document.querySelector('meta[name="latex-review-readonly"]')?.content === 'snapshot';
  const manifest = document.getElementById('translation-units');
  if (!manifest && !readonly) return;
  let units;
  try { units = JSON.parse(manifest ? manifest.textContent : 'null'); }
  catch (error) { if (!readonly) throw error; }
  const mode = document.getElementById('translation-mode');
  const status = document.getElementById('translation-status');
  const all = document.getElementById('translate-all');
  const cancel = document.getElementById('translation-cancel');
  const exportButton = document.getElementById('translation-export');
  let results = JSON.parse(document.getElementById('translation-results').textContent);
  const readableUnit = unit => unit && ['id', 'node_id', 'change_id', 'kind'].every(key => typeof unit[key] === 'string') &&
    ['old', 'new'].includes(unit.side);
  const legacyManifest = readonly && (!Array.isArray(units) || !units.every(readableUnit));
  if (legacyManifest) {
    // These are display identifiers from saved results, never a replacement source manifest.
    units = Object.values(results).filter(result => readableUnit(result) &&
      document.getElementById(result.side + '-' + result.node_id));
  }
  const blockedStates = Object.fromEntries(units.filter(unit => unit.error)
    .map(unit => [unit.id, {state: 'blocked', message: unit.error}]));
  let states = {...blockedStates}, token = '', active = false, polling = false, available = true;
  const installed = new Set();
  const live = document.body.classList.contains('live-view');
  const published = document.querySelector('meta[name="latex-review-publication"]')?.content === 'static';
  const localService = !readonly && !published && location.protocol === 'http:' && location.hostname === '127.0.0.1';
  const labels = {pending: '等待翻译', running: '正在翻译', ready: '译文已完成', failed: '翻译失败，可重试', cancelled: '已停止，可重试', blocked: '此段无法翻译'};
  let pinnedSentence = '', hoveredSentence = '';
  const liveCells = new Map();
  let savedTranslations;

  function unalignedTranslations() {
    if (!savedTranslations) {
      savedTranslations = document.createElement('section');
      savedTranslations.className = 'history-saved-translations';
      savedTranslations.setAttribute('aria-label', '已保存译文（只读，未关联原文）');
      const title = document.createElement('h2'); title.textContent = '已保存译文 · 只读';
      const explanation = document.createElement('p');
      explanation.textContent = '以下译文无法关联到当前报告的原文节点，仅展示保存时的内容，不提供原文跳转。';
      savedTranslations.append(title, explanation);
      document.body.append(savedTranslations);
    }
    return savedTranslations;
  }

  function readingAnchor() {
    if (!live) return null;
    const nodes = Array.from(document.querySelectorAll('.preview-side .review-node'));
    const node = nodes.find(node => {
      const rect = node.getBoundingClientRect();
      return rect.bottom > 120 && rect.top <= 120 && !node.querySelector('.review-node');
    }) || nodes.find(node => node.getBoundingClientRect().top > 120);
    return node ? {node, top: node.getBoundingClientRect().top} : null;
  }

  function paintSentences() {
    document.querySelectorAll('.sentence-linked,.sentence-pinned').forEach(node => {
      node.classList.remove('sentence-linked', 'sentence-pinned');
    });
    if (!live || mode.value !== 'bilingual') return;
    for (const id of new Set([hoveredSentence, pinnedSentence].filter(Boolean))) {
      const original = document.getElementById(id);
      const translated = document.querySelector('.translated-sentence[data-original-sentence="' + CSS.escape(id) + '"]');
      if (!original || !translated || translated.dataset.unmatched) continue;
      for (const node of [original, translated]) {
        node.classList.add('sentence-linked');
        node.classList.toggle('sentence-pinned', id === pinnedSentence);
      }
    }
  }

  function sentenceId(target) {
    if (!live || mode.value !== 'bilingual' || target.closest('a,button,input,select,textarea,[data-annotation-latex]')) return '';
    const sentence = target.closest('.review-sentence,.translated-sentence');
    if (!sentence || sentence.dataset.unmatched) return '';
    const id = sentence.dataset.originalSentence || sentence.id;
    return document.getElementById(id) && document.querySelector('.translated-sentence:not([data-unmatched])[data-original-sentence="' + CSS.escape(id) + '"]') ? id : '';
  }
  if (live) {
    document.addEventListener('pointerover', event => { hoveredSentence = sentenceId(event.target); paintSentences(); });
    document.addEventListener('pointerout', event => {
      hoveredSentence = event.relatedTarget instanceof Element ? sentenceId(event.relatedTarget) : '';
      paintSentences();
    });
    document.addEventListener('click', event => {
      if (!getSelection().isCollapsed) return;
      const id = sentenceId(event.target);
      if (id) { pinnedSentence = pinnedSentence === id ? '' : id; hoveredSentence = ''; paintSentences(); }
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { pinnedSentence = ''; hoveredSentence = ''; paintSentences(); }
    });
  }

  function showStatus(message) {
    status.textContent = message;
    status.hidden = false;
  }

  function applyResults() {
    const anchor = readingAnchor();
    Object.values(results).forEach(result => {
      if (installed.has(result.id)) return;
      const target = document.getElementById(result.side + '-' + result.node_id);
      if (!target && !readonly) return;
      const block = document.createElement('div');
      block.className = 'translation-block'; block.lang = 'zh-CN';
      block.dataset.translationUnit = result.id;
      const label = document.createElement('span'); label.className = 'translation-label'; label.textContent = '中文译文';
      block.append(label);
      result.sentences.forEach(sentence => {
        const span = document.createElement('span');
        const originalId = result.side + '-' + result.node_id + '-sentence-' + sentence.number;
        const aligned = !readonly || Boolean(target && Number.isInteger(sentence.number) && sentence.number > 0 &&
          target.contains(document.getElementById(originalId)) &&
          result.sentences.filter(item => item.number === sentence.number).length === 1);
        span.className = 'translated-sentence' + (sentence.changed && aligned ? ' sentence-changed' : '');
        if (aligned) span.dataset.originalSentence = originalId;
        else span.dataset.unmatched = 'true';
        // HTML is escaped by the server; protected fragments come from the existing safe preview renderer.
        span.innerHTML = sentence.html;
        block.append(span, document.createTextNode(' '));
      });
      if (!target) {
        block.style.display = 'block';
        unalignedTranslations().append(block);
        installed.add(result.id);
        return;
      }
      if (live && liveCells.has(result.id)) {
        const cell = liveCells.get(result.id);
        const originalIds = Array.from(cell.original.querySelectorAll('.review-sentence')).map(sentence => sentence.id);
        const translatedIds = result.sentences.map(sentence => result.side + '-' + result.node_id + '-sentence-' + sentence.number);
        const incomplete = originalIds.length !== translatedIds.length || new Set(translatedIds).size !== translatedIds.length ||
          translatedIds.some(id => !originalIds.includes(id));
        cell.incomplete = incomplete;
        block.querySelectorAll('.translated-sentence').forEach(sentence => {
          const id = sentence.dataset.originalSentence;
          if (!originalIds.includes(id) || translatedIds.filter(value => value === id).length !== 1) sentence.dataset.unmatched = 'true';
        });
      }
      function insert(container, translation) {
        if (live) {
          const cell = liveCells.get(result.id);
          if (cell) { cell.translation.prepend(translation); return; }
        }
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
    document.dispatchEvent(new Event('review-translations-updated'));
    paintSentences();
    if (anchor) window.scrollBy(0, anchor.node.getBoundingClientRect().top - anchor.top);
  }

  function renderTranslatedMath() {
    window.reviewMath?.enqueue();
  }
  window.addEventListener('review-math-ready', renderTranslatedMath);

  function refreshButtons() {
    document.querySelectorAll('[data-translate-change]').forEach(button => {
      if (readonly) { button.hidden = true; button.disabled = true; return; }
      const selected = units.filter(unit => unit.change_id === button.dataset.translateChange);
      const pending = selected.some(unit => ['running', 'pending'].includes((states[unit.id] || {}).state));
      const complete = selected.length > 0 && selected.every(unit => results[unit.id]);
      const incomplete = selected.some(unit => liveCells.get(unit.id)?.incomplete);
      const blocked = selected.every(unit => unit.error);
      button.disabled = pending || blocked || (!available && !complete);
      button.hidden = complete && !incomplete;
      button.textContent = blocked ? '此段无法翻译' : pending ? '正在翻译…' : incomplete ? '重试翻译' : '翻译此处';
      const text = button.parentElement.querySelector('.translation-item-status');
      if (text) text.textContent = selected.map(unit => {
        const state = states[unit.id] || (results[unit.id] ? {state: 'ready'} : {});
        return (live ? '' : unit.side === 'old' ? '修改前：' : '修改后：') + (liveCells.get(unit.id)?.incomplete ? '对应关系不完整' : labels[state.state] || '尚未翻译') +
          (state.message ? ' · ' + state.message : '');
      }).join('；');
      if (live && text) {
        text.hidden = complete && !incomplete && selected.every(unit => {
          const state = states[unit.id] || {state: 'ready'};
          return state.state === 'ready' && !state.message;
        });
        button.parentElement.hidden = button.hidden && text.hidden;
      }
    });
    document.querySelectorAll('.group-translation-side[data-translation-node],.inline-translation-side[data-translation-node]').forEach(side => {
      const id = side.dataset.translationNode;
      const placeholder = side.querySelector('.translation-placeholder');
      placeholder.hidden = readonly ? Boolean(side.querySelector('.translated-sentence')) : Boolean(results[id]);
      const state = states[id] || {};
      placeholder.textContent = labels[state.state] || '尚未翻译';
      if (state.message) placeholder.textContent += ' · ' + state.message;
    });
    cancel.disabled = !active;
    cancel.hidden = !active;
    const remaining = units.some(unit => !unit.error && !results[unit.id]);
    all.hidden = !remaining;
    all.disabled = active || !available || !remaining;
    const count = units.filter(unit => results[unit.id]).length;
    status.hidden = units.length > 0 && count === units.length && !active;
    const overview = document.getElementById('overview-translation');
    if (overview) {
      overview.textContent = legacyManifest ? '已保存译文 · ' + Object.keys(results).length + ' 段（旧清单不兼容）' :
        !units.length ? '无待翻译内容' : count === units.length ? '译文已完成 · ' + count + ' 段' :
        '译文 ' + count + ' / ' + units.length + ' 段' + (active ? ' · 翻译中' : '');
      overview.dataset.complete = String(!legacyManifest && units.length > 0 && count === units.length);
    }
    document.querySelectorAll('.group-translation,.inline-translation').forEach(section => {
      const hasResults = readonly ? Boolean(section.querySelector('.translated-sentence')) :
        Array.from(section.querySelectorAll('[data-translation-node]')).some(side => results[side.dataset.translationNode]);
      if (hasResults && !section.dataset.cachedTranslationOpened) {
        section.open = true;
        section.dataset.cachedTranslationOpened = 'true';
      }
    });
    if (readonly) {
      all.hidden = cancel.hidden = exportButton.hidden = true;
      all.disabled = cancel.disabled = exportButton.disabled = true;
      status.hidden = false;
    }
  }

  async function api(path, body) {
    if (!localService) throw new Error('请通过本机预览服务打开报告后继续翻译；已有译文仍可阅读。');
    const response = await fetch('api/translation/' + path, body === undefined ? {} : {
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
        if (liveCells.has(id)) liveCells.get(id).incomplete = false;
      }
    });
    document.getElementById('translation-results').textContent=JSON.stringify(incoming);
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
      (count ? '已保存 ' + count + ' / ' + units.length + ' 段译文；' +
        (count === units.length ? '全部译文可直接阅读。' : (data.message || '继续翻译需配置本机翻译服务。')) :
        (data.message || '请在项目配置中设置翻译接口后重新启动本机服务。'));
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
    } catch (error) { active = false; refreshButtons(); showStatus(error.message); }
    finally { polling = false; }
  }

  async function translate(ids, retry = false) {
    if (readonly) return;
    mode.value = 'bilingual'; applyResults();
    const remaining = ids.filter(id => (retry || !results[id]) && !blockedStates[id]);
    if (!remaining.length) return;
    try {
      const configuration = await api('status');
      accept(configuration);
      if (!available) throw new Error(configuration.message);
      accept(await api('start', {units: remaining, retry}));
      await poll();
    } catch (error) { showStatus(error.message); }
  }

  if (live) units.forEach(unit => {
    const node = document.getElementById(unit.id);
    if (!node) return;
    const controls = document.createElement('div'); controls.className = 'translation-item-controls';
    const button = document.createElement('button'); button.type = 'button'; button.dataset.translateChange = unit.change_id;
    const text = document.createElement('span'); text.className = 'translation-item-status'; text.setAttribute('role', 'status');
    controls.append(button, text);
    const original = unit.kind === 'paragraph' ? Array.from(node.children).find(child => child.tagName === 'P') :
      unit.kind === 'figure' ? node.querySelector('figcaption') : null;
    if (original) {
      const translation = document.createElement('div'); translation.className = 'live-translation-cell';
      node.classList.add('live-bilingual-pair');
      original.after(translation); translation.append(controls);
      original.lang = 'en';
      liveCells.set(unit.id, {original, translation, incomplete: false});
    } else {
      const heading = Array.from(node.children).find(child => /^H[1-6]$/.test(child.tagName));
      if (heading) heading.after(controls); else node.append(controls);
    }
  });
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
        const columns = document.createElement('div'); columns.className = 'comparison-columns'; translation.append(columns);
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
          side.append(caption, placeholder); columns.append(side);
        }
        translation.addEventListener('toggle', () => window.dispatchEvent(new Event('resize')));
        group.append(translation);
      });
      const translation = document.createElement('details'); translation.className = 'inline-translation';
      const summary = document.createElement('summary'); summary.textContent = '整段中文译文';
      translation.append(summary, controls.cloneNode(true));
      const columns = document.createElement('div'); columns.className = 'comparison-columns'; translation.append(columns);
      ['old', 'new'].forEach(sideName => {
        const unit = units.find(unit => unit.change_id === card.id && unit.side === sideName);
        const side = document.createElement('div'); side.className = 'inline-translation-side';
        if (unit) side.dataset.translationNode = unit.id;
        side.dataset.side = sideName;
        const label = document.createElement('p'); label.className = 'inline-heading';
        label.textContent = sideName === 'old' ? '修改前译文' : '修改后译文';
        const placeholder = document.createElement('p'); placeholder.className = 'translation-placeholder';
        placeholder.textContent = unit ? '尚未翻译' : '该侧无对应内容';
        side.append(label, placeholder); columns.append(side);
      });
      translation.addEventListener('toggle', () => window.dispatchEvent(new Event('resize')));
      inline.querySelector(':scope > summary').after(translation);
    }
  });
  document.addEventListener('click', event => {
    const sentence = event.target.closest('.translated-sentence.sentence-changed');
    if (sentence && !event.target.closest('a')) {
      window.reviewNavigation.sentence(sentence.dataset.originalSentence);
    }
    const button = event.target.closest('[data-translate-change]');
    if (!button) return;
    event.preventDefault();
    const inline = document.getElementById('inline-' + button.dataset.translateChange);
    const translation = button.closest('.group-translation') || (inline && inline.querySelector('.inline-translation'));
    if (translation) translation.open = true;
    const ids = units.filter(unit => unit.change_id === button.dataset.translateChange).map(unit => unit.id);
    translate(ids, ids.some(id => liveCells.get(id)?.incomplete));
  });
  all.addEventListener('click', () => translate(units.map(unit => unit.id)));
  cancel.addEventListener('click', async () => {
    try { accept(await api('cancel', {})); await poll(); } catch (error) { showStatus(error.message); }
  });
  mode.addEventListener('change', applyResults);
  exportButton.addEventListener('click', async () => {
    if (!localService) { showStatus('请通过本机服务导出含图片等阅读资源的译文报告。'); return; }
    try {
      const response = await fetch('api/translation/export');
      if (!response.ok) throw new Error('导出失败，请检查报告资源是否完整。');
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'report-translated.html';
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { showStatus(error.message); }
  });
  if (Object.keys(results).length) mode.value = 'bilingual';
  applyResults(); refreshButtons();
  if (!localService && Object.keys(results).length) {
    const count = units.filter(unit => results[unit.id]).length;
    status.textContent = count === units.length ? '已保存全部 ' + count + ' 段译文，可直接阅读。' :
      '已保存 ' + count + ' / ' + units.length + ' 段译文；继续翻译需启动本机服务。';
  }
  if (readonly) status.textContent = '历史快照只读 · 已保存 ' + Object.keys(results).length + ' 段译文';
  if (localService) poll();
})();
