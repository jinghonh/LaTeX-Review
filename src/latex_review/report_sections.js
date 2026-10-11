(function () {
  'use strict';
  const data = JSON.parse(document.getElementById('reader-data').textContent);
  const types = new Set(['part', 'chapter', 'section', 'subsection', 'subsubsection']);
  const panel = document.getElementById('sections-side');
  const toggle = document.getElementById('toggle-sections');
  const tree = document.getElementById('section-tree');
  const nodes = new Map(data.nodes.map(node => [node.side + '-' + node.id, node]));
  const headings = data.nodes.filter(node => types.has(node.type));
  const entries = [], byNode = new Map();
  const pairs = new Map(window.reviewNodePairs || []);
  function add(node, old) {
    const entry = {node, old, children: [], changes: new Set()};
    entries.push(entry);
    byNode.set(node.side + '-' + node.id, entry);
    if (old) byNode.set('old-' + old.id, entry);
    return entry;
  }
  headings.filter(node => node.side === 'new').forEach(node => {
    const old = headings.find(item => item.side === 'old' && (pairs.get(item.id) === node.id || (!pairs.has(item.id) && item.id === node.id)));
    add(node, old);
  });
  const oldHeadings = headings.filter(node => node.side === 'old');
  oldHeadings.forEach(node => { if (!byNode.has('old-' + node.id)) add(node, null); });
  const roots = [];
  entries.forEach(entry => {
    const parent = byNode.get(entry.node.side + '-' + entry.node.parent);
    entry.parent = parent;
    (parent ? parent.children : roots).push(entry);
  });
  // Insert deleted headings relative to the nearest surviving old sibling.
  entries.filter(entry => entry.node.side === 'old').forEach(entry => {
    const siblings = entry.parent ? entry.parent.children : roots;
    siblings.splice(siblings.indexOf(entry), 1);
    const index = oldHeadings.indexOf(entry.node);
    let next;
    for (const node of oldHeadings.slice(index + 1)) {
      const candidate = byNode.get('old-' + node.id);
      if (candidate && candidate.parent === entry.parent && siblings.includes(candidate)) { next = candidate; break; }
    }
    siblings.splice(next ? siblings.indexOf(next) : siblings.length, 0, entry);
  });
  function changed() { document.dispatchEvent(new Event('review-sections-changed')); }
  function open(value) {
    if (value && document.body.classList.contains('directory-open')) document.getElementById('close-changes').click();
    panel.hidden = !value;
    document.body.classList.toggle('sections-open', value);
    toggle.setAttribute('aria-expanded', String(value));
    window.dispatchEvent(new Event('resize'));
    changed();
  }
  function render(items, list, depth) {
    items.forEach(entry => {
      const li = document.createElement('li');
      const row = document.createElement('div'); row.className = 'section-row';
      const button = document.createElement('button'); button.type = 'button'; button.className = 'section-jump';
      const label = entry.node.section.at(-1) || '未命名章节';
      const name = document.createElement('span'); name.textContent = label + (entry.node.side === 'old' ? ' · 已删除' : '');
      const count = document.createElement('span'); count.className = 'section-count';
      button.append(name);
      if (!data.live) button.append(count); entry.button = button; entry.count = count;
      button.addEventListener('click', () => {
        if (window.reviewNavigation) window.reviewNavigation.chapter(entry.node.side, entry.node.id, entry.old?.id);
        else {
          const target = document.getElementById(entry.node.side + '-' + entry.node.id);
          const heading = Array.from(target.children).find(child => /^H[1-6]$/.test(child.tagName));
          (heading || target).scrollIntoView({block: 'start', behavior: 'auto'});
          target.tabIndex = -1; target.focus({preventScroll: true});
        }
        if (window.matchMedia('(max-width:1099px)').matches) open(false);
        highlight(entry); changed();
      });
      if (entry.children.length) {
        const details = document.createElement('details'); details.open = depth < 1;
        const summary = document.createElement('summary'); summary.setAttribute('aria-label', '展开或收起 ' + label);
        const nested = document.createElement('ol');
        summary.append(row); row.append(button); details.append(summary, nested); li.append(details);
        entry.details = details;
        button.addEventListener('click', event => event.preventDefault());
        details.addEventListener('toggle', changed);
        render(entry.children, nested, depth + 1);
      } else { row.append(button); li.append(row); }
      list.append(li);
    });
  }
  render(roots, tree, 0);
  document.querySelectorAll('.change-card').forEach(card => {
    const jump = card.querySelector('.change-jump');
    for (const side of ['old', 'new']) {
      let node = nodes.get(side + '-' + jump.dataset[side]);
      while (node) {
        const entry = byNode.get(side + '-' + node.id);
        if (entry) entry.changes.add(card.id);
        node = nodes.get(side + '-' + node.parent);
      }
    }
  });
  function counts() {
    entries.forEach(entry => {
      const pending = Array.from(entry.changes).filter(id => document.getElementById(id).dataset.reviewState === 'unreviewed').length;
      entry.count.textContent = entry.changes.size + ' · ' + pending;
      entry.count.title = entry.changes.size + ' 项变更，' + pending + ' 项未审阅';
    });
  }
  function highlight(active) {
    entries.forEach(entry => {
      if (entry === active) entry.button.setAttribute('aria-current', 'location');
      else entry.button.removeAttribute('aria-current');
    });
    for (let parent = active?.parent; parent; parent = parent.parent) if (parent.details) parent.details.open = true;
  }
  let scheduled = false;
  function track() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      const compare = document.getElementById('layout-mode')?.value === 'compare';
      const side = document.querySelector('.preview-side[data-side="new"]');
      const threshold = compare ? side.getBoundingClientRect().top + 40 : document.querySelector('.report-controls').getBoundingClientRect().bottom + 40;
      let active;
      headings.filter(node => node.side === 'new').forEach(node => {
        const target = document.getElementById('new-' + node.id);
        const title = target && Array.from(target.children).find(child => /^H[1-6]$/.test(child.tagName));
        if (!title || !title.getClientRects().length) return;
        // A chapter jump stops at its CSS scroll margin, which can exceed the toolbar height.
        const activationLine = compare ? threshold : Math.max(threshold, (parseFloat(getComputedStyle(title).scrollMarginTop) || 0) + 1);
        if (title.getBoundingClientRect().top <= activationLine) active = byNode.get('new-' + node.id);
      });
      if (active) highlight(active);
    });
  }
  window.addEventListener('scroll', track, {passive: true});
  document.querySelectorAll('.preview-side').forEach(side => side.addEventListener('scroll', track, {passive: true}));
  toggle.addEventListener('click', () => open(panel.hidden));
  document.getElementById('close-sections').addEventListener('click', () => { open(false); toggle.focus(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { open(false); toggle.focus(); } });
  window.reviewSections = {
    close() { open(false); }, counts,
    snapshot() { return {open: !panel.hidden, expanded: entries.filter(entry => entry.details?.open).map(entry => entry.node.side + '-' + entry.node.id)}; },
    restore(saved) {
      if (Array.isArray(saved?.expanded)) entries.forEach(entry => { if (entry.details) entry.details.open = saved.expanded.includes(entry.node.side + '-' + entry.node.id); });
      open(typeof saved?.open === 'boolean' ? saved.open : window.matchMedia('(min-width:1100px)').matches);
    }
  };
})();
