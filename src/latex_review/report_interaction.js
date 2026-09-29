(function () {
  'use strict';
  const cards = Array.from(document.querySelectorAll('.change-card'));
  const panels = Object.fromEntries(['old', 'new'].map(side =>
    [side, document.querySelector('.preview-side[data-side="' + side + '"]')]));
  const nodes = Object.fromEntries(['old', 'new'].map(side =>
    [side, Array.from(panels[side].querySelectorAll('.review-node'))]));
  const paired = {old: new Map(), new: new Map()};
  (window.reviewNodePairs || []).forEach(([oldId, newId]) => {
    paired.old.set(oldId, newId); paired.new.set(newId, oldId);
  });
  cards.forEach(card => {
    const jump = card.querySelector('.change-jump');
    if (jump.dataset.old && jump.dataset.new) {
      paired.old.set(jump.dataset.old, jump.dataset.new);
      paired.new.set(jump.dataset.new, jump.dataset.old);
    }
  });
  const kind = document.getElementById('kind-filter');
  const category = document.getElementById('category-filter');
  const mode = document.getElementById('reading-mode');
  const sync = document.getElementById('sync-scroll');
  const count = document.getElementById('filter-count');
  const status = document.getElementById('jump-status');
  let selected = -1;
  let mutedUntil = 0;

  for (const side of ['old', 'new']) {
    const empty = document.createElement('p');
    empty.className = 'side-empty'; empty.setAttribute('role', 'status');
    panels[side].insertBefore(empty, panels[side].querySelector('h2').nextSibling);
  }
  function visibleCards() { return cards.filter(card => !card.hidden); }
  function reviewNode(target, side) {
    const node = target && target.closest('.review-node');
    return node && panels[side].contains(node) ? node : null;
  }
  function revealAncestors(node, side) {
    for (let current = node; current && panels[side].contains(current);
         current = current.parentElement.closest('.review-node')) {
      current.classList.remove('context-hidden');
    }
  }
  function context() {
    const keep = {old: new Set(), new: new Set()};
    for (const card of visibleCards()) {
      const jump = card.querySelector('.change-jump');
      for (const side of ['old', 'new']) {
        const target = jump.dataset[side] && document.getElementById(side + '-' + jump.dataset[side]);
        const node = reviewNode(target, side);
        if (!node) continue;
        const siblings = nodes[side].filter(item => item.parentElement === node.parentElement);
        const index = siblings.indexOf(node);
        for (const neighbor of siblings.slice(Math.max(0, index - 1), index + 2)) keep[side].add(neighbor);
        for (let current = node; current && panels[side].contains(current);
             current = current.parentElement.closest('.review-node')) keep[side].add(current);
      }
    }
    for (const side of ['old', 'new']) {
      nodes[side].forEach(node => {
        node.classList.toggle('context-hidden', mode.value === 'context' && !keep[side].has(node));
      });
    }
  }
  function filter() {
    let shown = 0;
    cards.forEach(card => {
      const visible = (kind.value === 'all' || card.dataset.kind === kind.value) &&
        (category.value === 'all' || card.dataset.categories.split(' ').includes(category.value));
      card.hidden = !visible; if (visible) shown++;
    });
    count.textContent = '显示 ' + shown + ' / ' + cards.length + ' 项主变更';
    if (selected >= 0 && cards[selected].hidden) selected = -1;
    context();
  }
  kind.addEventListener('change', filter);
  category.addEventListener('change', filter);
  mode.addEventListener('change', context);
  filter();

  function jump(button) {
    document.querySelectorAll('.preview-side .is-highlighted').forEach(node => node.classList.remove('is-highlighted'));
    const messages = [];
    for (const [side, label] of [['old', '修改前'], ['new', '修改后']]) {
      const panel = panels[side];
      const empty = panel.querySelector('.side-empty');
      const id = button.dataset[side];
      const node = id ? document.getElementById(side + '-' + id) : null;
      if (node) {
        if (mode.value === 'context') {
          const target = reviewNode(node, side);
          if (target) revealAncestors(target, side);
        }
        empty.classList.remove('is-visible');
        node.classList.add('is-highlighted');
        panel.scrollTop += node.getBoundingClientRect().top - panel.getBoundingClientRect().top - panel.clientHeight / 3;
      } else {
        empty.textContent = label + '侧无对应节点'; empty.classList.add('is-visible');
      }
      messages.push(label + '：' + button.dataset[side + 'Location']);
    }
    mutedUntil = performance.now() + 350;
    status.textContent = messages.join('；');
  }
  document.getElementById('changes-side').addEventListener('click', event => {
    const copy = event.target.closest('.copy-source');
    if (copy) {
      const value = copy.dataset.location;
      function legacyCopy() {
        const input = document.createElement('textarea');
        input.value = value; input.style.position = 'fixed'; input.style.opacity = '0';
        document.body.appendChild(input); input.select();
        const done = document.execCommand('copy'); input.remove();
        status.textContent = done ? '已复制：' + value : '无法自动复制，请从卡片位置文字复制。';
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(
          () => { status.textContent = '已复制：' + value; }, legacyCopy);
      } else legacyCopy();
      return;
    }
    const button = event.target.closest('button[data-old][data-new]');
    if (!button) return;
    const card = button.closest('.change-card');
    selected = cards.indexOf(card);
    jump(button);
  });
  function step(direction) {
    const visible = visibleCards();
    if (!visible.length) { status.textContent = '当前筛选没有变更。'; return; }
    let index = visible.indexOf(cards[selected]);
    index = Math.max(0, Math.min(visible.length - 1, index + direction));
    if (selected < 0) index = direction > 0 ? 0 : visible.length - 1;
    const card = visible[index];
    selected = cards.indexOf(card);
    card.scrollIntoView({block: 'nearest', behavior: 'auto'});
    card.querySelector('.change-jump').focus({preventScroll: true});
    jump(card.querySelector('.change-jump'));
  }
  document.getElementById('previous-change').addEventListener('click', () => step(-1));
  document.getElementById('next-change').addEventListener('click', () => step(1));
  document.addEventListener('keydown', event => {
    const target = event.target;
    if (event.defaultPrevented || target.isContentEditable ||
        /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
    if (event.altKey && !event.ctrlKey && !event.metaKey &&
        (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
      event.preventDefault(); step(event.key === 'ArrowUp' ? -1 : 1);
    }
  });

  function visibleNodes(side) {
    return nodes[side].filter(node => node.getClientRects().length > 0);
  }
  function nearestAnchor(side, active, reference) {
    const other = side === 'old' ? 'new' : 'old';
    function match(node) {
      const id = paired[side].get(node.dataset.nodeId);
      const counterpart = id && document.getElementById(other + '-' + id);
      return counterpart && counterpart.getClientRects().length > 0 ? [node, counterpart] : null;
    }
    const direct = match(active);
    if (direct) return direct;
    const distance = node => {
      const rect = node.getBoundingClientRect();
      return Math.max(rect.top - reference, reference - rect.bottom, 0);
    };
    const near = candidates => candidates.map(match).filter(Boolean).sort((a, b) =>
      distance(a[0]) - distance(b[0]))[0];
    const visible = visibleNodes(side);
    const sibling = near(visible.filter(node => node.parentElement === active.parentElement));
    if (sibling) return sibling;
    const adjacent = near(visible.filter(node => node !== active && !node.contains(active) && !active.contains(node)));
    if (adjacent) return adjacent;
    for (let parent = active.parentElement.closest('.review-node'); parent;
         parent = parent.parentElement.closest('.review-node')) {
      const fallback = match(parent);
      if (fallback) return fallback;
    }
    return null;
  }
  function align(side) {
    if (!sync.checked || performance.now() < mutedUntil || window.matchMedia('(max-width:1000px)').matches) return;
    const panel = panels[side];
    const other = panels[side === 'old' ? 'new' : 'old'];
    const reference = panel.getBoundingClientRect().top + panel.clientHeight / 2;
    const candidates = visibleNodes(side);
    if (!candidates.length) return;
    const depth = node => {
      let result = 0;
      for (let parent = node.parentElement.closest('.review-node'); parent;
           parent = parent.parentElement.closest('.review-node')) result++;
      return result;
    };
    const distance = node => {
      const rect = node.getBoundingClientRect();
      return Math.max(rect.top - reference, reference - rect.bottom, 0);
    };
    const score = node => distance(node) + Math.min(node.getBoundingClientRect().height, 500) * .1;
    const active = candidates.reduce((best, node) =>
      score(node) < score(best) || (score(node) === score(best) && depth(node) > depth(best)) ? node : best);
    const anchor = nearestAnchor(side, active, reference);
    if (!anchor) return;
    const [from, to] = anchor;
    const fraction = Math.max(0, Math.min(1, (reference - from.getBoundingClientRect().top) / Math.max(1, from.offsetHeight)));
    const desired = other.scrollTop + to.getBoundingClientRect().top - other.getBoundingClientRect().top +
      fraction * to.offsetHeight - other.clientHeight / 2;
    if (Math.abs(desired - other.scrollTop) > 2) {
      mutedUntil = performance.now() + 180;
      other.scrollTop = desired;
    }
  }
  for (const side of ['old', 'new']) {
    let scheduled = false;
    panels[side].addEventListener('scroll', () => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; align(side); });
    }, {passive: true});
  }
})();
