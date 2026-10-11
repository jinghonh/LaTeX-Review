(function () {
  'use strict';
  const data = JSON.parse(document.getElementById('reader-data').textContent);
  const cards = Array.from(document.querySelectorAll('.change-card'));
  const ids = new Set(cards.map(card => card.id));
  const key = 'latex-review-reader-v1:' + data.fingerprint;
  const storageStatus = document.getElementById('review-storage-status');
  const progress = document.getElementById('review-progress');
  const stateInput = document.getElementById('review-state');
  const noteInput = document.getElementById('review-note');
  const reviewFilter = document.getElementById('review-filter');
  const stateLabels = {unreviewed: '未审阅', reviewed: '已审阅', pending: '待处理'};
  const navigation = window.reviewNavigation;
  let records = Object.create(null), current = null, ui = {}, restoring = true;
  function validRecords(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('审阅记录格式无效。');
    const result = Object.create(null);
    for (const [id, record] of Object.entries(value)) {
      if (!ids.has(id) || !record || !Object.hasOwn(stateLabels, record.state) ||
          typeof record.note !== 'string' || record.note.length > 10000) throw new Error('审阅记录包含无效变更或备注。');
      result[id] = {state: record.state, note: record.note};
    }
    return result;
  }
  function readEnvelope(value) {
    if (!value || value.version !== 1 || value.fingerprint !== data.fingerprint)
      throw new Error('备份不属于当前报告，或备份版本不受支持。');
    return validRecords(value.records);
  }
  try {
    const saved = localStorage.getItem(key);
    if (saved) {
      const value = JSON.parse(saved);
      records = readEnvelope(value);
      ui = value.ui && typeof value.ui === 'object' ? value.ui : {};
    }
  } catch (error) {
    storageStatus.textContent = '无法恢复浏览器记录：' + error.message + ' 本次仍可审阅并导出备份。';
  }
  function snapshot() {
    const fields = ['kind-filter', 'category-filter', 'reading-mode', 'layout-mode', 'translation-mode', 'review-filter', 'section-filter'];
    const values = Object.fromEntries(fields.map(id => [id, document.getElementById(id).value]));
    return {...values, sync: document.getElementById('sync-scroll').checked,
      selected: navigation.selected(), scrollY: window.scrollY,
      readerOpen: document.getElementById('reader-tools').open, query: document.getElementById('report-search').value,
      sections: window.reviewSections.snapshot(),
      directoryOpen: document.body.classList.contains('directory-open'),
      panels: Object.fromEntries(['old', 'new'].map(side => [side,
        document.querySelector('.preview-side[data-side="' + side + '"]').scrollTop]))};
  }
  function envelope() { return {version: 1, fingerprint: data.fingerprint, records, ui: snapshot()}; }
  function persist() {
    if (restoring) return;
    try { localStorage.setItem(key, JSON.stringify(envelope())); return true; }
    catch (error) { storageStatus.textContent = '浏览器无法保存记录；请导出 JSON 备份。'; return false; }
  }
  const editors = new Map();
  function renderProgress() {
    let reviewed = 0, pending = 0;
    cards.forEach(card => {
      const state = records[card.id]?.state || 'unreviewed';
      card.dataset.reviewState = state;
      if (state === 'reviewed') reviewed++;
      if (state === 'pending') pending++;
      let badge = card.querySelector('.review-badge');
      if (!badge) { badge = document.createElement('span'); badge.className = 'review-badge'; card.append(badge); }
      badge.textContent = stateLabels[state];
      (editors.get(card.id) || []).forEach(editor => {
        editor.state.value = state;
        editor.note.value = records[card.id]?.note || '';
      });
    });
    window.reviewSections.counts();
    progress.textContent = '已审阅 ' + reviewed + ' / ' + cards.length + ' · 待处理 ' + pending;
  }
  function select(id) {
    current = ids.has(id) ? id : null;
    stateInput.disabled = noteInput.disabled = !current;
    stateInput.value = records[current]?.state || 'unreviewed';
    noteInput.value = records[current]?.note || '';
    document.getElementById('review-current').textContent = current ?
      '当前：第 ' + (cards.findIndex(card => card.id === current) + 1) + ' 项主变更' : '先选择一项主变更。';
  }
  document.addEventListener('review-selection', event => { select(event.detail); persist(); });
  function edit() {
    if (!current) return;
    records[current] = {state: stateInput.value, note: noteInput.value};
    renderProgress(); persist();
    if (reviewFilter.value !== 'all') navigation.filter();
  }
  stateInput.addEventListener('change', edit);
  noteInput.addEventListener('input', edit);
  reviewFilter.addEventListener('change', () => { navigation.filter(); persist(); });
  cards.forEach((card, index) => {
    const controls = [];
    for (const container of [card, document.getElementById('inline-' + card.id)]) {
      if (!container) continue;
      const editor = document.createElement('div'); editor.className = 'change-review-editor';
      const state = document.createElement('select');
      state.setAttribute('aria-label', '第 ' + (index + 1) + ' 项主变更审阅状态');
      for (const [value, label] of Object.entries(stateLabels)) {
        const option = document.createElement('option'); option.value = value; option.textContent = label; state.append(option);
      }
      const note = document.createElement('textarea'); note.rows = 2; note.maxLength = 10000;
      note.placeholder = '记录审阅备注'; note.setAttribute('aria-label', '第 ' + (index + 1) + ' 项主变更备注');
      editor.append(state, note); container.append(editor); controls.push({state, note});
      function save() {
        records[card.id] = {state: state.value, note: note.value};
        renderProgress(); select(navigation.selected()); persist();
        if (reviewFilter.value !== 'all') navigation.filter();
      }
      state.addEventListener('change', save); note.addEventListener('input', save);
    }
    editors.set(card.id, controls);
  });
  document.getElementById('review-export').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(envelope(), null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = 'review-' + data.fingerprint.slice(0, 12) + '.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.getElementById('review-import').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('备份超过 5 MiB。');
      const incoming = readEnvelope(JSON.parse(await file.text()));
      let added = 0, retained = 0;
      for (const [id, record] of Object.entries(incoming)) {
        if (Object.hasOwn(records, id)) retained++;
        else { records[id] = record; added++; }
      }
      renderProgress(); select(current); navigation.filter();
      const saved = persist();
      storageStatus.textContent = '导入完成：补充 ' + added + ' 项，保留本地 ' + retained + ' 项。' +
        (saved ? '' : ' 浏览器无法保存记录；请导出 JSON 备份。');
    } catch (error) { storageStatus.textContent = '导入失败：' + error.message; }
    event.target.value = '';
  });

  // Original index is captured before translations and excludes nested nodes and review excerpts.
  const searchItems = [];
  data.nodes.forEach(item => {
    const target = document.getElementById(item.side + '-' + item.id);
    if (!target?.classList.contains('review-node')) return;
    const copy = target.cloneNode(true);
    copy.querySelectorAll('.review-node,.change-anchor,.inline-change,.change-review-editor,.translation-block,.deletion-marker,.change-trigger').forEach(node => {
      // Change triggers can wrap original text, so remove only their interactive semantics.
      if (node.classList.contains('change-trigger')) node.replaceWith(...node.childNodes);
      else node.remove();
    });
    searchItems.push({...item, text: (copy.textContent || "") + ' ' + item.citations.join(' ')});
  });
  const searchInput = document.getElementById('report-search');
  const sectionFilter = document.getElementById('section-filter');
  const searchStatus = document.getElementById('search-status');
  const results = document.getElementById('search-results');
  const sections = new Map();
  searchItems.forEach(item => {
    item.sectionKey = JSON.stringify(item.section);
    sections.set(item.sectionKey, item.section.join(' / ') || '正文前置内容');
  });
  for (const [value, label] of sections) {
    const option = document.createElement('option'); option.value = value; option.textContent = label; sectionFilter.append(option);
  }
  function search() {
    const query = searchInput.value.trim().toLocaleLowerCase();
    results.replaceChildren();
    if (!query && sectionFilter.value === 'all') { searchStatus.textContent = '输入文字，或选择章节。'; return; }
    let found = 0;
    searchItems.forEach(item => {
      if (sectionFilter.value !== 'all' && sectionFilter.value !== item.sectionKey) return;
      const target = document.getElementById(item.side + '-' + item.id);
      const translatedText = Array.from(target.querySelectorAll('.translation-block'))
        .filter(block => block.closest('.review-node') === target).map(block => block.textContent).join(' ');
      const originalMatch = !query || item.text.toLocaleLowerCase().includes(query);
      const translatedMatch = query && translatedText.toLocaleLowerCase().includes(query);
      if (!originalMatch && !translatedMatch) return;
      found++;
      const text = originalMatch ? item.text : translatedText;
      const index = query ? text.toLocaleLowerCase().indexOf(query) : 0;
      const snippet = text.slice(Math.max(0, index - 35), Math.max(0, index - 35) + 160).trim();
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = (item.side === 'old' ? '修改前' : '修改后') + ' · ' + sections.get(item.sectionKey) +
        (originalMatch ? '' : ' · 中文译文') + '：' + snippet;
      button.addEventListener('click', () => { navigation.node(item.side, item.id, !originalMatch); persist(); });
      const li = document.createElement('li'); li.append(button); results.append(li);
    });
    searchStatus.textContent = found ? '找到 ' + found + ' 处内容。' : '没有匹配内容。';
  }
  searchInput.addEventListener('input', () => { search(); persist(); });
  sectionFilter.addEventListener('change', () => { search(); persist(); });
  document.addEventListener('review-translations-updated', search);

  window.reviewSections.restore(ui.sections);
  document.addEventListener('review-sections-changed', persist);
  renderProgress();
  for (const id of ['kind-filter', 'category-filter', 'reading-mode', 'layout-mode', 'translation-mode', 'review-filter', 'section-filter']) {
    const field = document.getElementById(id);
    if (Array.from(field.options).some(option => option.value === ui[id])) field.value = ui[id];
    field.addEventListener('change', persist);
  }
  document.getElementById('reader-tools').open = ui.readerOpen === true;
  if (typeof ui.query === 'string') searchInput.value = ui.query;
  search();
  document.getElementById('reader-tools').addEventListener('toggle', persist);
  for (const id of ['toggle-changes', 'close-changes'])
    document.getElementById(id).addEventListener('click', persist);
  if (ui.directoryOpen === true) document.getElementById('toggle-changes').click();
  document.getElementById('sync-scroll').checked = ui.sync === true;
  document.getElementById('sync-scroll').addEventListener('change', persist);
  document.getElementById('layout-mode').dispatchEvent(new Event('change'));
  document.getElementById('reading-mode').dispatchEvent(new Event('change'));
  navigation.filter();
  if (typeof ui.selected === 'string') navigation.select(ui.selected);
  function restorePosition() {
    for (const side of ['old', 'new']) {
      const value = ui.panels?.[side];
      if (Number.isFinite(value) && value >= 0) document.querySelector('.preview-side[data-side="' + side + '"]').scrollTop = value;
    }
    if (Number.isFinite(ui.scrollY) && ui.scrollY >= 0) window.scrollTo(0, ui.scrollY);
    restoring = false;
  }
  if (document.readyState === 'complete') requestAnimationFrame(restorePosition);
  else window.addEventListener('load', () => requestAnimationFrame(restorePosition), {once: true});
  let timer;
  function scheduleSave() { clearTimeout(timer); timer = setTimeout(persist, 200); }
  window.addEventListener('scroll', scheduleSave, {passive: true});
  document.querySelectorAll('.preview-side').forEach(panel => panel.addEventListener('scroll', scheduleSave, {passive: true}));
  window.addEventListener('pagehide', persist);
})();
