(() => {
  const paper = document.querySelector('.preview-side');
  const search = document.getElementById('live-search');
  const sections = paper ? Array.from(paper.querySelectorAll('.node-part,.node-chapter,.node-section,.node-subsection,.node-subsubsection')) : [];
  const heading = node => node.querySelector('h1,h2,h3,h4')?.textContent.trim() || '';
  const storageKey = 'latex-live:' + location.pathname;
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) {}
  const menu = document.getElementById('live-sections');
  if (menu) {
    sections.forEach(node => {
      const option = document.createElement('option');
      option.value = node.id; option.textContent = heading(node); menu.append(option);
    });
    menu.addEventListener('change', () => jump(document.getElementById(menu.value)));
    menu.disabled = !sections.length;
  }
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
    const matches = query && paper ? Array.from(paper.querySelectorAll('.review-node')).filter(node => {
      return node.textContent.toLocaleLowerCase().includes(query) &&
        !Array.from(node.querySelectorAll('.review-node')).some(child => child.textContent.toLocaleLowerCase().includes(query));
    }) : [];
    matches.forEach(node => {
      const item = document.createElement('li'), button = document.createElement('button');
      button.type = 'button'; button.textContent = node.textContent.trim().replace(/\s+/g, ' ').slice(0, 160);
      button.addEventListener('click', () => jump(node)); item.append(button); results.append(item);
    });
    document.getElementById('live-search-status').textContent = query ? `找到 ${matches.length} 处。` : '输入文字搜索当前论文。';
  }
  if (search) { search.value = saved?.query || ''; search.addEventListener('input', find); find(); }
  const panel = document.getElementById('live-navigation');
  if (panel && saved) panel.open = !!saved.open;
  function remember() {
    const near = sections.filter(node => node.getBoundingClientRect().top <= 120).at(-1);
    const state = {scroll:window.scrollY, query:search?.value || '', open:panel?.open || false,
      section:near ? heading(near) : '', index:sections.indexOf(near),
      occurrence:near ? sections.slice(0, sections.indexOf(near)).filter(item => heading(item) === heading(near)).length : 0,
      offset:near ? near.getBoundingClientRect().top : 0};
    try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) {}
  }
  if (saved) history.scrollRestoration = 'manual';
  let restoring = !!saved;
  function restore() {
    if (!restoring) return;
    const matches = sections.filter(item => heading(item) === saved.section);
    const node = matches[Math.min(saved.occurrence || 0, matches.length - 1)] ||
      (saved.index >= 0 ? sections[Math.min(saved.index, sections.length - 1)] : null);
    window.scrollTo(0, node ? window.scrollY + node.getBoundingClientRect().top - saved.offset : saved.scroll || 0);
  }
  window.addEventListener('load', restore);
  window.addEventListener('review-math-ready', restore);
  for (const event of ['pointerdown', 'wheel', 'keydown', 'touchstart']) {
    window.addEventListener(event, () => { restoring = false; }, {once:true, passive:true});
  }
  document.querySelector('.live-controls>a')?.addEventListener('click', () => {
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
      document.getElementById('live-status').textContent = state.message;
      if (state.revision !== revision) { remember(); location.reload(); }
    } catch (_) { document.getElementById('live-status').textContent = '预览服务连接中断。'; }
  }
  check(); setInterval(check, 1000);
})();
