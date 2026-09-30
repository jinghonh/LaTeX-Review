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
  const layout = document.getElementById('layout-mode');
  const inlineChanges = new Map();
  let selected = -1;
  function readingView() { return layout && layout.value === 'reading'; }
  function updateSelection(card) {
    selected = cards.indexOf(card);
    if (!layout) return;
    cards.forEach(item => item.setAttribute('aria-current', String(item === card)));
    document.getElementById('current-change').textContent = card ?
      '第 ' + (selected + 1) + ' / ' + cards.length + ' 处变更' : '尚未选择变更';
  }
  function updateInlineVisibility() {
    inlineChanges.forEach((detail, card) => {
      detail.hidden = card.hidden;
      if (card.hidden) detail.open = false;
    });
  }
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
  function retainContext(keep, side, node) {
    const siblings = nodes[side].filter(item => item.parentElement === node.parentElement);
    const index = siblings.indexOf(node);
    for (const neighbor of siblings.slice(Math.max(0, index - 1), index + 2)) keep[side].add(neighbor);
    for (let current = node; current && panels[side].contains(current);
         current = current.parentElement.closest('.review-node')) keep[side].add(current);
  }
  function pairedContextAnchor(side, node) {
    const other = side === 'old' ? 'new' : 'old';
    const match = candidate => {
      const id = paired[side].get(candidate.dataset.nodeId);
      const counterpart = id && document.getElementById(other + '-' + id);
      const review = reviewNode(counterpart, other);
      return review ? [candidate, review] : null;
    };
    const nearest = candidates => {
      const index = candidates.indexOf(node);
      if (index < 0) return null;
      for (let offset = 0; offset < candidates.length; offset++) {
        for (const position of [index - offset, index + offset]) {
          if (position < 0 || position >= candidates.length) continue;
          const pair = match(candidates[position]);
          if (pair) return pair;
        }
      }
      return null;
    };
    const siblings = nodes[side].filter(item => item.parentElement === node.parentElement);
    const nearby = nearest(siblings);
    if (nearby) return nearby;
    const index = nodes[side].indexOf(node);
    for (let offset = 1; offset < nodes[side].length; offset++) {
      for (const position of [index - offset, index + offset]) {
        if (position < 0 || position >= nodes[side].length) continue;
        const candidate = nodes[side][position];
        if (candidate.contains(node) || node.contains(candidate)) continue;
        const adjacent = match(candidate);
        if (adjacent) return adjacent;
      }
    }
    for (let parent = node.parentElement.closest('.review-node'); parent;
         parent = parent.parentElement.closest('.review-node')) {
      const fallback = match(parent);
      if (fallback) return fallback;
    }
    return null;
  }
  function context() {
    const keep = {old: new Set(), new: new Set()};
    if (mode.value !== 'context') {
      for (const side of ['old', 'new']) {
        nodes[side].forEach(node => node.classList.remove('context-hidden'));
        const empty = panels[side].querySelector('.side-empty');
        if (empty.dataset.contextNotice === 'true') {
          empty.classList.remove('is-visible');
          delete empty.dataset.contextNotice;
        }
      }
      return;
    }
    for (const card of visibleCards()) {
      const jump = card.querySelector('.change-jump');
      const targets = {};
      for (const side of ['old', 'new']) {
        const target = jump.dataset[side] && document.getElementById(side + '-' + jump.dataset[side]);
        targets[side] = reviewNode(target, side);
        if (targets[side]) retainContext(keep, side, targets[side]);
      }
      if (!!targets.old !== !!targets.new) {
        const side = targets.old ? 'old' : 'new';
        const other = side === 'old' ? 'new' : 'old';
        const anchor = pairedContextAnchor(side, targets[side]);
        if (anchor) {
          retainContext(keep, side, anchor[0]);
          retainContext(keep, other, anchor[1]);
        }
      }
    }
    for (const side of ['old', 'new']) {
      nodes[side].forEach(node => {
        node.classList.toggle('context-hidden', mode.value === 'context' && !keep[side].has(node));
      });
      const empty = panels[side].querySelector('.side-empty');
      if (mode.value === 'context' && keep[side].size === 0) {
        empty.textContent = '此侧没有可对齐的上下文节点';
        empty.dataset.contextNotice = 'true';
        empty.classList.add('is-visible');
      } else if (empty.dataset.contextNotice === 'true') {
        empty.classList.remove('is-visible');
        delete empty.dataset.contextNotice;
      }
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
    if (selected >= 0 && cards[selected].hidden) updateSelection(null);
    updateInlineVisibility();
    context();
  }
  kind.addEventListener('change', filter);
  category.addEventListener('change', filter);
  mode.addEventListener('change', context);
  filter();

  function jump(button) {
    const card = button.closest('.change-card') || cards.find(item =>
      inlineChanges.get(item)?.contains(button));
    if (card) updateSelection(card);
    const detail = card && inlineChanges.get(card);
    if (readingView() && detail) {
      detail.open = true;
      for (let parent = detail.parentElement; parent && parent !== panels.new; parent = parent.parentElement)
        parent.classList.remove('context-hidden');
    }
    document.querySelectorAll('.preview-side .is-highlighted').forEach(node => node.classList.remove('is-highlighted'));
    document.querySelectorAll('.preview-side .is-cell-highlighted').forEach(node => node.classList.remove('is-cell-highlighted'));
    const messages = [];
    const sentenceJump = button.classList.contains('detail-jump') &&
      (button.dataset.oldSentences || button.dataset.newSentences);
    for (const [side, label] of [['old', '修改前'], ['new', '修改后']]) {
      const panel = panels[side];
      const empty = panel.querySelector('.side-empty');
      const id = button.dataset[side];
      const sentenceIds = sentenceJump ? (button.dataset[side + 'Sentences'] || '').split(' ').filter(Boolean) : [];
      const sentenceNodes = sentenceIds.map(value => document.getElementById(side + '-' + value));
      const precise = sentenceIds.length > 0 && sentenceNodes.every(Boolean);
      const node = precise ? sentenceNodes[0] : !sentenceJump && id ? document.getElementById(side + '-' + id) : null;
      if (node) {
        if (mode.value === 'context') {
          const target = reviewNode(node, side);
          if (target) revealAncestors(target, side);
        }
        empty.classList.remove('is-visible');
        node.classList.add('is-highlighted');
        if (precise) sentenceNodes.slice(1).forEach(sentence => sentence.classList.add('is-highlighted'));
        if (!readingView()) panel.scrollTop += node.getBoundingClientRect().top - panel.getBoundingClientRect().top - panel.clientHeight / 3;
        const row = button.dataset[side + 'Row'];
        const column = button.dataset[side + 'Column'];
        if (row || column) {
          node.querySelectorAll('td[data-row][data-column]').forEach(cell => {
            if ((!row || cell.dataset.row === row) && (!column || cell.dataset.column === column)) {
              cell.classList.add('is-cell-highlighted');
            }
          });
        }
      } else {
        empty.textContent = sentenceJump ? label + '侧无可精确定位的对应句子' : label + '侧无对应节点';
        empty.classList.add('is-visible');
      }
      messages.push(label + '：' + (precise ? '已定位完整句子；' : sentenceJump ? '句子定位不可确认；' : '') + button.dataset[side + 'Location']);
    }
    mutedUntil = performance.now() + 350;
    status.textContent = messages.join('；');
    if (readingView() && detail) {
      detail.querySelectorAll('[data-original-id]').forEach(copy => {
        const original = document.getElementById(copy.dataset.originalId);
        copy.classList.toggle('is-highlighted', !!original?.classList.contains('is-highlighted'));
        copy.classList.toggle('is-cell-highlighted', !!original?.classList.contains('is-cell-highlighted'));
      });
      const row = button.dataset.oldRow;
      const column = button.dataset.oldColumn;
      detail.querySelectorAll('.old-excerpt td[data-row][data-column]').forEach(cell => {
        cell.classList.toggle('is-cell-highlighted', !!(row || column) &&
          (!row || cell.dataset.row === row) && (!column || cell.dataset.column === column));
      });
      detail.scrollIntoView({block: 'nearest', behavior: 'auto'});
    }
  }
  document.getElementById('changes-side').addEventListener('click', event => {
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
    const focusTarget = readingView() ? inlineChanges.get(card)?.querySelector('summary') : card.querySelector('.change-jump');
    if (focusTarget) focusTarget.focus({preventScroll: true});
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
    if (readingView() || !sync.checked || performance.now() < mutedUntil || window.matchMedia('(max-width:1000px)').matches) return;
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

  if (layout) {
    const directoryButton = document.getElementById('toggle-changes');
    function directory(open) {
      document.body.classList.toggle('directory-open', open);
      directoryButton.setAttribute('aria-expanded', String(open));
    }
    directoryButton.addEventListener('click', () => directory(directoryButton.getAttribute('aria-expanded') !== 'true'));
    document.getElementById('changes-side').addEventListener('click', event => {
      if (event.target.closest('button[data-old][data-new]') && window.matchMedia('(max-width:1099px)').matches) {
        directory(false);
        const detail = inlineChanges.get(cards[selected]);
        if (readingView() && detail) detail.querySelector('summary').focus({preventScroll: true});
      }
    });
    document.getElementById('close-changes').addEventListener('click', () => {
      directory(false); directoryButton.focus();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && document.body.classList.contains('directory-open')) {
        directory(false); directoryButton.focus();
      }
    });
    layout.addEventListener('change', () => {
      document.body.classList.toggle('compare-view', !readingView());
      document.body.classList.toggle('reading-view', readingView());
      if (selected >= 0) jump(cards[selected].querySelector('.change-jump'));
    });
    // Snapshot nodes before inserting details so excerpts never become navigation anchors.
    const tails = new Map();
    function insertDetail(detail, target, before = false) {
      const tail = tails.get(target);
      if (tail) tail.after(detail);
      else if (before) target.before(detail);
      else target.after(detail);
      tails.set(target, detail);
    }
    function deletionAnchor(oldNode) {
      if (!oldNode) return null;
      const siblings = nodes.old.filter(node => node.parentElement === oldNode.parentElement);
      const index = siblings.indexOf(oldNode);
      for (let distance = 1; distance < siblings.length; distance++) {
        for (const position of [index - distance, index + distance]) {
          const candidate = siblings[position];
          const id = candidate && paired.old.get(candidate.dataset.nodeId);
          const target = id && document.getElementById('new-' + id);
          if (target) return {target, before: position > index};
        }
      }
      return null;
    }
    cards.forEach((card, index) => {
      const button = card.querySelector('.change-jump');
      const target = button.dataset.new && document.getElementById('new-' + button.dataset.new);
      const oldNode = button.dataset.old && document.getElementById('old-' + button.dataset.old);
      const detail = document.createElement('details');
      detail.className = 'inline-change'; detail.id = 'inline-' + card.id;
      const summary = document.createElement('summary');
      const kindText = button.querySelector('.kind').textContent;
      const label = document.createElement('span');
      label.textContent = kindText + ' · ' + String(index + 1).padStart(2, '0');
      const hint = document.createElement('span'); hint.className = 'change-label';
      hint.textContent = card.dataset.kind === 'removed' ? '查看已删除内容' : '查看旧文与明细';
      summary.append(label, hint); detail.append(summary);
      const heading = document.createElement('p'); heading.className = 'inline-heading';
      const categories = Array.from(card.querySelectorAll('.badge')).map(badge => badge.textContent).join(' · ');
      heading.textContent = categories + ' · ' + kindText;
      const content = target || oldNode;
      const lead = content && content.querySelector('h2,h3,h4,p,figcaption');
      let excerptTitle = '';
      if (lead) {
        const text = lead.cloneNode(true);
        text.querySelectorAll('.math-tex').forEach(math => { math.textContent = '〔公式〕'; });
        excerptTitle = text.textContent.replace(/\s+/g, ' ').trim();
      }
      button.querySelector('strong').textContent = excerptTitle ?
        excerptTitle.slice(0, 64) + (excerptTitle.length > 64 ? '…' : '') : categories + '内容';
      detail.append(heading);
      if (oldNode) {
        const title = document.createElement('p'); title.className = 'inline-heading'; title.textContent = '修改前';
        const excerpt = document.createElement('div'); excerpt.className = 'old-excerpt';
        const copy = oldNode.cloneNode(true);
        [copy, ...copy.querySelectorAll('*')].forEach(element => {
          if (element.id) {
            element.dataset.originalId = element.id;
            element.id = detail.id + '-' + element.id;
          }
          element.classList.remove('review-node', 'context-hidden', 'is-highlighted');
          element.removeAttribute('data-node-id');
        });
        excerpt.append(copy); detail.append(title, excerpt);
      } else {
        const note = document.createElement('p'); note.textContent = '此处为新增内容，旧稿没有对应内容。'; detail.append(note);
      }
      const list = card.querySelector('.detail-list').cloneNode(true);
      detail.append(list);
      const pages = card.querySelector('.card-pages');
      if (pages) detail.append(pages.cloneNode(true));
      const close = document.createElement('button'); close.type = 'button';
      close.className = 'inline-close'; close.textContent = '收起对照 ↑';
      close.addEventListener('click', () => { detail.open = false; summary.focus(); });
      detail.append(close);
      detail.addEventListener('toggle', () => { if (detail.open) updateSelection(card); });
      detail.addEventListener('click', event => {
        const jumpButton = event.target.closest('.detail-jump');
        if (jumpButton) jump(jumpButton);
      });
      if (target) {
        // Structural headings wrap their descendants; place their marker after the heading.
        const sectionHeading = Array.from(target.children).find(child => /^H[1-6]$/.test(child.tagName));
        if (sectionHeading) insertDetail(detail, sectionHeading);
        else if (target.tagName === 'LI') target.append(detail);
        else insertDetail(detail, target);
      } else {
        const anchor = deletionAnchor(oldNode);
        if (anchor && anchor.target.tagName !== 'LI') insertDetail(detail, anchor.target, anchor.before);
        else {
          const note = document.createElement('p');
          note.className = 'inline-heading'; note.textContent = '新稿中没有可确定的对应位置。';
          detail.insertBefore(note, heading); panels.new.append(detail);
        }
      }
      inlineChanges.set(card, detail);
    });
    cards.sort((a, b) => {
      if (a === b) return 0;
      return inlineChanges.get(a).compareDocumentPosition(inlineChanges.get(b)) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
    cards.forEach((card, index) => {
      document.getElementById('changes-side').append(card);
      inlineChanges.get(card).querySelector('summary span').textContent =
        card.querySelector('.kind').textContent + ' · ' + String(index + 1).padStart(2, '0');
    });
    updateInlineVisibility();
    // Page links may target content inside collapsed supplements or a closed directory.
    document.addEventListener('click', event => {
      const link = event.target.closest('a[href^="#"]');
      if (!link) return;
      const target = document.getElementById(link.getAttribute('href').slice(1));
      if (!target) return;
      if (target.classList.contains('change-card')) directory(true);
      for (let parent = target.parentElement; parent; parent = parent.parentElement)
        if (parent.tagName === 'DETAILS') parent.open = true;
    }, true);
  }
})();
