(() => {
  const paper = document.querySelector('.preview-side');
  const search = document.getElementById('live-search');
  const sections = paper ? Array.from(paper.querySelectorAll('.node-part,.node-chapter,.node-section,.node-subsection,.node-subsubsection')) : [];
  const heading = node => {
    const title = node.querySelector('h1,h2,h3,h4')?.cloneNode(true);
    title?.querySelector('.structure-number')?.remove();
    return title?.textContent.trim() || '';
  };
  const storageKey = 'latex-live:' + location.pathname;
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) {}
  function jump(node) {
    if (!node) return;
    node.scrollIntoView({block:'start'});
    node.setAttribute('tabindex', '-1'); node.focus({preventScroll:true});
  }
  function find() {
    const results = document.getElementById('live-results');
    if (!results) return;
    results.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase();
    const matches = query ? window.reviewSearch.find(query) : [];
    matches.forEach(match => {
      const node = match.target;
      const item = document.createElement('li'), button = document.createElement('button');
      button.type = 'button'; button.textContent = (match.translated ? '中文译文：' : '') + match.snippet.replace(/\s+/g, ' ');
      button.addEventListener('click', () => {
        if (match.translated) {
          mode.value = 'bilingual'; mode.dispatchEvent(new Event('change'));
        }
        jump(node);
      }); item.append(button); results.append(item);
    });
    document.getElementById('live-search-status').textContent = query ? `找到 ${matches.length} 处。` : '输入文字搜索当前论文。';
  }
  if (search) { search.value = saved?.query || ''; search.addEventListener('input', find); find(); }
  const panel = document.getElementById('live-navigation');
  if (panel && saved) panel.open = !!saved.open;
  const mode = document.getElementById('translation-mode');
  if (mode && ['original', 'bilingual'].includes(saved?.mode)) {
    mode.value = saved.mode;
    mode.dispatchEvent(new Event('change'));
  }
  window.reviewSections.restore(saved?.sections);
  function remember() {
    const paragraphs = Array.from(paper.querySelectorAll('.node-paragraph,.node-figure'));
    const near = paragraphs.filter(node => node.getBoundingClientRect().top <= 120).at(-1) ||
      sections.filter(node => node.getBoundingClientRect().top <= 120).at(-1);
    const state = {sections: window.reviewSections.snapshot(), scroll:window.scrollY, query:search?.value || '', open:panel?.open || false,
      mode:mode?.value, paragraph:near && paragraphs.includes(near) ? near.id : null,
      paragraphIndex:paragraphs.indexOf(near), section:near ? heading(near) : '', index:sections.indexOf(near),
      occurrence:near ? sections.slice(0, sections.indexOf(near)).filter(item => heading(item) === heading(near)).length : 0,
      offset:near ? near.getBoundingClientRect().top : 0};
    try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) {}
  }
  if (saved) history.scrollRestoration = 'manual';
  let restoring = !!saved;
  function restore() {
    if (!restoring) return;
    const matches = sections.filter(item => heading(item) === saved.section);
    const paragraphs = Array.from(paper.querySelectorAll('.node-paragraph,.node-figure'));
    const node = (saved.paragraph && (document.getElementById(saved.paragraph) || paragraphs[saved.paragraphIndex])) ||
      matches[Math.min(saved.occurrence || 0, matches.length - 1)] ||
      (saved.index >= 0 ? sections[Math.min(saved.index, sections.length - 1)] : null);
    window.scrollTo(0, node ? window.scrollY + node.getBoundingClientRect().top - saved.offset : saved.scroll || 0);
  }
  window.addEventListener('load', restore);
  window.addEventListener('review-math-ready', restore);
  document.addEventListener('review-translations-updated', () => { find(); restore(); });
  for (const event of ['pointerdown', 'wheel', 'keydown', 'touchstart']) {
    window.addEventListener(event, () => { restoring = false; }, {once:true, passive:true});
  }
  document.querySelector('.live-controls a[href="#live-diagnostics"]')?.addEventListener('click', () => {
    const diagnostics = document.getElementById('live-diagnostics');
    diagnostics.open = true;
  });
  window.addEventListener('pagehide', remember);
  if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1') return;
  let revision = LIVE_REVISION;
  async function check() {
    try {
      const response = await fetch('live-status.json', {cache:'no-store'});
      if (!response.ok) throw new Error('status');
      const state = await response.json();
      const status = document.getElementById('live-status');
      status.textContent = state.message;
      status.hidden = state.message.startsWith('持续预览已启动') || state.message.startsWith('已更新当前论文');
      if (state.revision !== revision && !window.reviewAnnotations?.isBusy()) { remember(); location.reload(); }
    } catch (_) {
      const status = document.getElementById('live-status');
      status.textContent = '预览服务连接中断。'; status.hidden = false;
    }
  }
  check(); setInterval(check, 1000);
})();
