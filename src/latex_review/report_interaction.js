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
  const inlineAnchors = new Map();
  const changeTriggers = new Map();
  let arrangeInlineChanges = () => {};
  let selected = -1;
  function readingView() { return layout && layout.value === 'reading'; }
  function updateSelection(card) {
    selected = cards.indexOf(card);
    if (!layout) return;
    cards.forEach(item => item.setAttribute('aria-current', String(item === card)));
    inlineChanges.forEach((detail, item) => detail.setAttribute('aria-current', String(item === card)));
    document.getElementById('current-change').textContent = card ?
      '第 ' + (selected + 1) + ' / ' + cards.length + ' 处变更' : '尚未选择变更';
  }
  function updateInlineVisibility() {
    inlineChanges.forEach((detail, card) => {
      detail.hidden = card.hidden;
      inlineAnchors.get(card).slot.hidden = card.hidden;
      if (card.hidden) detail.open = false;
    });
    updateTriggerState();
    arrangeInlineChanges();
  }
  function updateTriggerState() {
    changeTriggers.forEach((triggers, card) => {
      const expanded = readingView() && inlineChanges.get(card).open;
      triggers.forEach(trigger => {
        trigger.setAttribute('aria-expanded', String(expanded));
        trigger.classList.toggle('is-active-change', expanded && cards[selected] === card);
        if (trigger.classList.contains('change-trigger')) {
          trigger.tabIndex = readingView() ? 0 : -1;
          if (readingView()) {
            trigger.setAttribute('role', 'button');
            trigger.setAttribute('aria-label', '查看第 ' + (cards.indexOf(card) + 1) + ' 处修改前后对比');
          } else {
            trigger.removeAttribute('role'); trigger.removeAttribute('aria-label');
          }
        }
      });
    });
  }
  function openInlineChange(card) {
    inlineChanges.forEach((detail, item) => { detail.open = item === card; });
    updateTriggerState();
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
      arrangeInlineChanges();
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
    arrangeInlineChanges();
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
      openInlineChange(card);
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
      let selectedDetail = null;
      detail.querySelectorAll('.detail-list:not(.supplemental-details) .detail-jump').forEach(copy => {
        const matches = sentenceJump && copy.dataset.oldSentences === button.dataset.oldSentences &&
          copy.dataset.newSentences === button.dataset.newSentences;
        copy.classList.toggle('is-selected-detail', !!matches);
        if (matches && !selectedDetail) selectedDetail = copy;
      });
      if (button.classList.contains('detail-jump') && !sentenceJump)
        detail.querySelector('.inline-full-context').open = true;
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
      arrangeInlineChanges();
      const toolbar = document.querySelector('.report-controls');
      detail.style.scrollMarginTop = (toolbar && getComputedStyle(toolbar).position === 'sticky' ? toolbar.offsetHeight + 16 : 16) + 'px';
      detail.scrollIntoView({block: 'start', behavior: 'auto'});
      if (selectedDetail) {
        selectedDetail.style.scrollMarginTop = detail.style.scrollMarginTop;
        selectedDetail.scrollIntoView({block: 'nearest', behavior: 'auto'});
      }
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
      arrangeInlineChanges();
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
      updateTriggerState();
      arrangeInlineChanges();
      if (selected >= 0) jump(cards[selected].querySelector('.change-jump'));
    });
    // Snapshot nodes before inserting details so excerpts never become navigation anchors.
    const tails = new Map();
    function insertDetail(slot, target, before = false) {
      const tail = tails.get(target);
      if (tail) tail.after(slot);
      else if (before) target.before(slot);
      else target.after(slot);
      tails.set(target, slot);
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
    // Compare the already-safe preview DOM. Math and references stay indivisible,
    // and cloned inline formatting never passes through an HTML string builder.
    function diffTokens(source) {
      const tokens = [];
      if (!source) return tokens;
      let firstText = true;
      function visit(node, wrappers) {
        if (node.nodeType === Node.TEXT_NODE) {
          let text = node.textContent;
          if (firstText) { text = text.replace(/^修改[前后]：/, ''); firstText = false; }
          const parts = text.match(/\s+|\p{Script=Han}|[\p{L}\p{N}_]+|[^\s]/gu) || [];
          parts.forEach(part => {
            const value = /^\s+$/.test(part) ? ' ' : part;
            tokens.push({key: wrappers.map(item => item.tagName).join('/') + ':' + value,
              node: document.createTextNode(value), wrappers, space: value === ' '});
          });
          return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.matches('.math-tex,.citation,.cross-ref,.unresolved-ref,.preview-unavailable')) {
          firstText = false;
          tokens.push({key: node.className + ':' + node.textContent, node, wrappers, space: false});
          return;
        }
        node.childNodes.forEach(child => visit(child, [...wrappers, node]));
      }
      source.childNodes.forEach(node => visit(node, []));
      return tokens;
    }
    function diffOperations(before, after) {
      const n = before.length, m = after.length;
      const operations = [];
      function add(kind, a, b, c, d) {
        const previous = operations[operations.length - 1];
        if (previous && previous.kind === kind) { previous.b = b; previous.d = d; }
        else operations.push({kind, a, b, c, d});
      }
      // Bound work for unusually long paragraphs; their common edges still
      // remain context, and the differing middle is shown without losing text.
      if (n * m > 250000) {
        let start = 0, end = 0;
        while (start < n && start < m && before[start].key === after[start].key) start++;
        while (end < n - start && end < m - start && before[n - end - 1].key === after[m - end - 1].key) end++;
        add('equal', 0, start, 0, start);
        add('replace', start, n - end, start, m - end);
        add('equal', n - end, n, m - end, m);
        return operations.filter(op => op.a !== op.b || op.c !== op.d);
      }
      const lengths = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
      for (let a = n - 1; a >= 0; a--) for (let c = m - 1; c >= 0; c--)
        lengths[a][c] = before[a].key === after[c].key ? lengths[a + 1][c + 1] + 1 :
          Math.max(lengths[a + 1][c], lengths[a][c + 1]);
      let a = 0, c = 0;
      while (a < n || c < m) {
        if (a < n && c < m && before[a].key === after[c].key) {
          add('equal', a, a + 1, c, c + 1); a++; c++;
        } else if (c < m && (a === n || lengths[a][c + 1] > lengths[a + 1][c])) {
          add('insert', a, a, c, c + 1); c++;
        } else { add('delete', a, a + 1, c, c); a++; }
      }
      return operations;
    }
    function compactDiff(beforeSource, afterSource, firstNumber) {
      const before = diffTokens(beforeSource), after = diffTokens(afterSource);
      let operations = diffOperations(before, after);
      // A source-only change may have identical displayed labels. Keep the
      // complete sentence visible rather than presenting an empty comparison.
      if (!operations.some(op => op.kind !== 'equal'))
        operations = [{kind: 'replace', a: 0, b: before.length, c: 0, d: after.length}];
      function edge(tokens, position, direction) {
        let count = 0;
        while ((position > 0 && direction < 0) || (position < tokens.length && direction > 0)) {
          const token = tokens[direction < 0 ? --position : position++];
          if (!token.space && ++count === 6) break;
        }
        return position;
      }
      const groups = [];
      operations.filter(op => op.kind !== 'equal').forEach(op => {
        const range = {a: edge(before, op.a, -1), b: edge(before, op.b, 1),
          c: edge(after, op.c, -1), d: edge(after, op.d, 1)};
        const previous = groups[groups.length - 1];
        if (previous && range.a <= previous.b && range.c <= previous.d) {
          previous.b = range.b; previous.d = range.d;
        } else groups.push(range);
      });
      const comparison = document.createElement('div'); comparison.className = 'compact-diff';
      function snippet(tokens, start, end, side) {
        const text = document.createElement('span'); text.className = 'diff-snippet';
        if (!tokens.length) { text.classList.add('diff-empty'); text.textContent = '该侧无对应内容'; return text; }
        if (start) text.append(document.createTextNode('… '));
        let run, changedBefore;
        for (let index = start; index < end; index++) {
          const changed = operations.some(op => op.kind !== 'equal' &&
            index >= op[side === 'old' ? 'a' : 'c'] && index < op[side === 'old' ? 'b' : 'd']);
          if (!run || changed !== changedBefore) {
            run = document.createElement(changed ? side === 'old' ? 'del' : 'ins' : 'span');
            run.className = changed ? 'diff-' + (side === 'old' ? 'removed' : 'added') : 'diff-context';
            text.append(run); changedBefore = changed;
          }
          const token = tokens[index];
          let copy = token.node.cloneNode(true);
          for (const wrapper of [...token.wrappers].reverse()) {
            const parent = wrapper.cloneNode(false); parent.removeAttribute('id');
            parent.append(copy); copy = parent;
          }
          run.append(copy);
        }
        if (end < tokens.length) text.append(document.createTextNode(' …'));
        return text;
      }
      groups.forEach((range, index) => {
        const group = document.createElement('div'); group.className = 'diff-group';
        const title = document.createElement('span'); title.className = 'diff-group-title';
        title.textContent = '改动 ' + (firstNumber + index);
        group.append(title);
        for (const [side, label, tokens, start, end] of [
          ['old', '修改前', before, range.a, range.b], ['new', '修改后', after, range.c, range.d]]) {
          const row = document.createElement('span'); row.className = 'diff-row diff-row-' + side;
          const caption = document.createElement('span'); caption.className = 'diff-side-label'; caption.textContent = label;
          if (tokens.length) row.append(caption, snippet(tokens, start, end, side));
          else {
            row.classList.add('diff-row-empty'); caption.textContent += ' · 无对应内容'; row.append(caption);
          }
          group.append(row);
        }
        comparison.append(group);
      });
      return comparison;
    }
    cards.forEach((card, index) => {
      const button = card.querySelector('.change-jump');
      const target = button.dataset.new ? document.getElementById('new-' + button.dataset.new) : null;
      const oldNode = button.dataset.old ? document.getElementById('old-' + button.dataset.old) : null;
      const slot = document.createElement('div'); slot.className = 'change-anchor';
      const detail = document.createElement('details');
      detail.className = 'inline-change'; detail.id = 'inline-' + card.id;
      detail.dataset.kind = card.dataset.kind;
      slot.append(detail);
      const summary = document.createElement('summary');
      const kindText = button.querySelector('.kind').textContent;
      const label = document.createElement('span');
      label.className = 'change-kind kind-' + card.dataset.kind;
      label.textContent = kindText + ' · ' + String(index + 1).padStart(2, '0');
      const hint = document.createElement('span'); hint.className = 'change-label';
      hint.textContent = '查看具体改动';
      summary.append(label, hint); detail.append(summary);
      const heading = document.createElement('p'); heading.className = 'inline-heading';
      const categories = Array.from(card.querySelectorAll('.badge')).map(badge => badge.textContent).join(' · ');
      heading.textContent = categories;
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
      const list = card.querySelector('.detail-list').cloneNode(true);
      const supplement = document.createElement('ul'); supplement.className = 'detail-list supplemental-details';
      const seenSentences = new Set();
      const covered = {old: new Set(), new: new Set()};
      const sentenceDetails = Array.from(list.querySelectorAll('.detail-jump'));
      const textDetail = button => button.dataset.detailCategory === 'text' ||
        (!button.dataset.detailCategory && button.textContent.trim().startsWith('正文 ·'));
      sentenceDetails.sort((a, b) => Number(textDetail(b)) - Number(textDetail(a)));
      let groupCount = 0;
      sentenceDetails.forEach(jumpButton => {
        const pair = jumpButton.querySelector('.sentence-pair');
        if (!pair) return;
        const ids = Object.fromEntries(['old', 'new'].map(side =>
          [side, (jumpButton.dataset[side + 'Sentences'] || '').split(' ').filter(Boolean)]));
        const key = jumpButton.dataset.oldSentences + '|' + jumpButton.dataset.newSentences;
        const alreadyShown = !textDetail(jumpButton) && ['old', 'new'].every(side => ids[side].every(id => covered[side].has(id)));
        if (seenSentences.has(key) || alreadyShown) {
          pair.hidden = true; supplement.append(jumpButton.closest('li')); return;
        }
        seenSentences.add(key);
        ['old', 'new'].forEach(side => ids[side].forEach(id => covered[side].add(id)));
        const comparison = compactDiff(jumpButton.dataset.oldSentences ? pair.querySelector('.sentence-before') : null,
          jumpButton.dataset.newSentences ? pair.querySelector('.sentence-after') : null, groupCount + 1);
        groupCount += comparison.children.length;
        comparison.querySelectorAll('.diff-group').forEach(group => {
          const trigger = jumpButton.cloneNode(false);
          trigger.append(...Array.from(group.childNodes)); group.append(trigger);
        });
        jumpButton.replaceWith(comparison);
      });
      if (!groupCount && card.dataset.kind !== 'moved' &&
          (oldNode?.classList.contains('node-paragraph') || target?.classList.contains('node-paragraph'))) {
        const comparison = compactDiff(oldNode?.querySelector('p'), target?.querySelector('p'), 1);
        groupCount = comparison.children.length; detail.append(comparison);
      }
      hint.textContent = groupCount ? groupCount + ' 处改动' : '查看变更明细';
      detail.append(list);
      const full = document.createElement('details'); full.className = 'inline-full-context';
      const fullSummary = document.createElement('summary'); fullSummary.textContent = '完整段落对照';
      full.append(fullSummary); detail.append(full);
      const fullColumns = document.createElement('div'); fullColumns.className = 'comparison-columns';
      full.append(fullColumns);
      function appendExcerpt(node, side) {
        const column = document.createElement('div'); column.className = 'comparison-side'; column.dataset.side = side;
        const title = document.createElement('p'); title.className = 'inline-heading';
        title.textContent = side === 'old' ? '修改前' : '修改后';
        column.append(title); fullColumns.append(column);
        if (!node) {
          const note = document.createElement('p'); note.className = 'comparison-empty';
          note.textContent = side === 'old' ? '此处为新增内容，旧稿没有对应内容。' : '此处内容已从新稿删除。';
          column.append(note); return;
        }
        const excerpt = document.createElement('div'); excerpt.className = side + '-excerpt';
        const copy = node.cloneNode(true);
        copy.querySelectorAll('.change-anchor').forEach(anchor => anchor.remove());
        [copy, ...copy.querySelectorAll('*')].forEach(element => {
          if (element.id) {
            element.dataset.originalId = element.id;
            element.id = detail.id + '-' + element.id;
          }
          element.classList.remove('review-node', 'context-hidden', 'is-highlighted');
          element.removeAttribute('data-node-id');
        });
        excerpt.append(copy); column.append(excerpt);
      }
      appendExcerpt(oldNode, 'old');
      appendExcerpt(target, 'new');
      if (supplement.children.length) full.append(supplement);
      full.addEventListener('toggle', () => arrangeInlineChanges());
      const pages = card.querySelector('.card-pages');
      if (pages) detail.append(pages.cloneNode(true));
      const close = document.createElement('button'); close.type = 'button';
      close.className = 'inline-close'; close.textContent = '收起对照 ↑';
      close.addEventListener('click', () => {
        detail.open = false; updateTriggerState(); arrangeInlineChanges(); summary.focus();
      });
      detail.append(close);
      summary.addEventListener('click', event => {
        event.preventDefault();
        if (detail.open) {
          detail.open = false; updateTriggerState(); arrangeInlineChanges();
        } else jump(button);
      });
      detail.addEventListener('toggle', () => {
        if (detail.open) { updateSelection(card); openInlineChange(card); }
        updateTriggerState(); arrangeInlineChanges();
      });
      detail.addEventListener('click', event => {
        const jumpButton = event.target.closest('.detail-jump');
        if (jumpButton) jump(jumpButton);
      });
      let alignmentTarget = target;
      if (target) {
        // Structural headings wrap their descendants; place their marker after the heading.
        const sectionHeading = Array.from(target.children).find(child => /^H[1-6]$/.test(child.tagName));
        if (sectionHeading) { insertDetail(slot, sectionHeading); alignmentTarget = sectionHeading; }
        else if (target.tagName === 'LI') target.append(slot);
        else insertDetail(slot, target);
      } else {
        const anchor = deletionAnchor(oldNode);
        if (anchor && anchor.target.tagName !== 'LI') insertDetail(slot, anchor.target, anchor.before);
        else {
          const note = document.createElement('p');
          note.className = 'inline-heading'; note.textContent = '新稿中没有可确定的对应位置。';
          detail.insertBefore(note, heading); panels.new.append(slot);
        }
        alignmentTarget = slot;
      }
      inlineChanges.set(card, detail);
      inlineAnchors.set(card, {slot, target: alignmentTarget});
      changeTriggers.set(card, new Set());
      if (card.dataset.kind === 'removed') {
        const marker = document.createElement('button'); marker.type = 'button';
        marker.className = 'deletion-marker';
        marker.textContent = '− 删除 · ' + String(index + 1).padStart(2, '0');
        marker.setAttribute('aria-controls', detail.id);
        marker.addEventListener('click', () => jump(button));
        slot.classList.add('has-deletion'); slot.prepend(marker);
        changeTriggers.get(card).add(marker);
      }
    });
    cards.sort((a, b) => {
      if (a === b) return 0;
      return inlineAnchors.get(a).slot.compareDocumentPosition(inlineAnchors.get(b).slot) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
    cards.forEach((card, index) => {
      document.getElementById('changes-side').append(card);
      inlineChanges.get(card).querySelector('summary span').textContent =
        card.querySelector('.kind').textContent + ' · ' + String(index + 1).padStart(2, '0');
      const marker = inlineAnchors.get(card).slot.querySelector('.deletion-marker');
      if (marker) marker.textContent = '− 删除 · ' + String(index + 1).padStart(2, '0');
    });
    // Associate sentence anchors with their precise detail, never by matching displayed text.
    const sentenceButtons = new Map();
    cards.forEach((card, index) => {
      card.querySelectorAll('.detail-jump').forEach(button => {
        (button.dataset.newSentences || '').split(' ').filter(Boolean).forEach(id => {
          const sentence = document.getElementById('new-' + id);
          if (!sentence || !sentence.classList.contains('sentence-changed') || sentenceButtons.has(sentence)) return;
          sentenceButtons.set(sentence, button);
          sentence.classList.add('change-trigger');
          sentence.setAttribute('role', 'button'); sentence.tabIndex = 0;
          sentence.setAttribute('aria-controls', inlineChanges.get(card).id);
          sentence.setAttribute('aria-label', '查看第 ' + (index + 1) + ' 处修改前后对比');
          sentence.title = '点击查看修改前后对比';
          changeTriggers.get(card).add(sentence);
        });
      });
    });
    function activateSentence(sentence) {
      const button = sentenceButtons.get(sentence);
      if (!button || !readingView()) return;
      if (button.closest('.change-card').hidden) {
        kind.value = 'all'; category.value = 'all'; filter();
      }
      jump(button);
    }
    panels.new.addEventListener('click', event => {
      if (event.target.closest('a,button,summary,input,select')) return;
      const sentence = event.target.closest('.change-trigger');
      if (sentence) activateSentence(sentence);
    });
    panels.new.addEventListener('keydown', event => {
      if (event.target.classList.contains('change-trigger') && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault(); activateSentence(event.target);
      }
    });
    // Keep notes beside their source; expanded or adjacent notes move down without overlapping.
    arrangeInlineChanges = () => {
      const availableWidth = innerWidth - (document.body.classList.contains('directory-open') ? 340 : 0);
      const marginNotes = readingView() && availableWidth >= 1240;
      document.body.classList.toggle('margin-notes', marginNotes);
      const main = document.querySelector('main');
      main.style.setProperty('--margin-overflow', '0px');
      if (!marginNotes) return;
      const paper = panels.new.getBoundingClientRect();
      let bottom = paper.top;
      visibleCards().forEach(card => {
        const detail = inlineChanges.get(card);
        const anchor = inlineAnchors.get(card);
        if (!anchor.slot.getClientRects().length) return;
        const position = anchor.slot.getBoundingClientRect();
        const top = Math.max(anchor.target.getBoundingClientRect().top, bottom + 12);
        detail.style.setProperty('--change-left', (paper.right + 24 - position.left) + 'px');
        detail.style.setProperty('--change-top', (top - position.top) + 'px');
        bottom = top + detail.getBoundingClientRect().height;
      });
      main.style.setProperty('--margin-overflow', Math.max(0, bottom - paper.bottom) + 'px');
    };
    let arranging = false;
    function scheduleInlineLayout() {
      if (arranging) return;
      arranging = true;
      requestAnimationFrame(() => { arranging = false; arrangeInlineChanges(); });
    }
    window.addEventListener('resize', scheduleInlineLayout);
    panels.new.addEventListener('load', scheduleInlineLayout, true);
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(scheduleInlineLayout);
      observer.observe(panels.new);
      inlineChanges.forEach(detail => observer.observe(detail));
    }
    if (document.fonts) document.fonts.ready.then(scheduleInlineLayout);
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
