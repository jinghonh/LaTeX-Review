/* Preserve a visible character across width changes, including long paragraphs. */
(() => {
  'use strict';
  const papers = [...document.querySelectorAll('.preview-side')];
  if (!papers.length) return;
  const content = '.node-paragraph p,.node-figure figcaption,.table-caption,.table-preview td,.node-equation,h1,h2,h3,h4,h5,h6,.translation-block';
  const excluded = '.annotation-card,.annotation-editor,.translation-item-controls,.inline-change,.old-excerpt,button';
  let anchors = [], dimensions = '', frame = 0, restoring = false, settleFrame = 0, stableFrames = 0;
  const mode = () => document.getElementById('layout-mode')?.value || 'reading';
  let readingMode = mode();
  const expectedScroll = new Map();
  const nativeAnchoring = new Map([document.documentElement, ...papers].map(node => [node, node.style.overflowAnchor]));
  function enableNativeAnchoring(enabled) {
    for (const [node, original] of nativeAnchoring) node.style.overflowAnchor = enabled ? original : 'none';
  }
  function signature() {
    return [innerWidth, innerHeight, ...papers.flatMap(p => [p.clientWidth,
      getComputedStyle(p).overflowY])].join(':');
  }
  function scroller(paper) {
    const style = getComputedStyle(paper);
    return /auto|scroll/.test(style.overflowY) && paper.scrollHeight > paper.clientHeight ? paper : window;
  }
  function viewport(paper, scroll) {
    const rect = paper.getBoundingClientRect();
    if (scroll !== window) return {top: Math.max(0, rect.top + paper.clientTop), bottom: Math.min(innerHeight, rect.bottom)};
    const toolbar = document.querySelector('.report-controls');
    const bar = toolbar?.getBoundingClientRect();
    const top = bar && getComputedStyle(toolbar).position === 'sticky' ? Math.max(0, bar.bottom) : 0;
    return {top, bottom: innerHeight};
  }
  function textRange(element, x, y) {
    const caret = document.caretPositionFromPoint?.(x, y);
    let range;
    if (caret) {
      range = document.createRange(); range.setStart(caret.offsetNode, caret.offset); range.collapse(true);
    } else range = document.caretRangeFromPoint?.(x, y);
    if (!range || range.startContainer.nodeType !== Node.TEXT_NODE || !element.contains(range.startContainer)) return null;
    const parent = range.startContainer.parentElement;
    if (parent.closest(excluded) || !parent.closest(content)) return null;
    // A one-character range has a stable line box after wrapping changes.
    const offset = Math.min(range.startOffset, range.startContainer.length - 1);
    if (offset < 0) return null;
    range.setStart(range.startContainer, offset); range.setEnd(range.startContainer, offset + 1);
    return range;
  }
  function nearestTextRange(element, y) {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let text, best = null, distance = Infinity;
    while ((text = walker.nextNode())) {
      if (!text.textContent.trim() || text.parentElement.closest(excluded + ',mjx-container')) continue;
      const range = document.createRange(); range.selectNodeContents(text);
      if (!range.getClientRects().length) continue;
      const bounds = range.getBoundingClientRect();
      const gap = Math.max(bounds.top - y, y - bounds.bottom, 0);
      if (gap > distance) continue;
      let left = 0, right = text.length - 1;
      while (left < right) {
        const middle = Math.floor((left + right) / 2);
        range.setStart(text, middle); range.setEnd(text, middle + 1);
        if (range.getBoundingClientRect().bottom < y) left = middle + 1;
        else right = middle;
      }
      range.setStart(text, left); range.setEnd(text, left + 1);
      const rect = range.getBoundingClientRect();
      const delta = Math.max(rect.top - y, y - rect.bottom, 0);
      if (delta < distance) { best = range; distance = delta; }
      if (distance === 0) break;
    }
    return best;
  }
  function capture(paper) {
    if (!paper.getClientRects().length) return null;
    const scroll = scroller(paper), view = viewport(paper, scroll), rect = paper.getBoundingClientRect();
    if (view.bottom <= view.top || rect.bottom < view.top || rect.top > view.bottom) return null;
    const y = view.top + (view.bottom - view.top) * .35;
    const x = Math.max(1, Math.min(innerWidth - 1, rect.left + rect.width * .4));
    let range = textRange(paper, x, y);
    let element = range?.startContainer.parentElement.closest(content);
    if (!element) {
      let distance = Infinity;
      for (const node of paper.querySelectorAll(content)) {
        if (node.closest(excluded) || !node.getClientRects().length) continue;
        const bounds = node.getBoundingClientRect();
        const delta = Math.max(bounds.top - y, y - bounds.bottom, 0);
        if (delta < distance) { distance = delta; element = node; }
      }
    }
    if (!element) return null;
    if (!range) range = nearestTextRange(element, y);
    const bounds = range?.getBoundingClientRect() || element.getBoundingClientRect();
    return {paper, scroll, range, element, ratio: (bounds.top - view.top) / (view.bottom - view.top)};
  }
  function remember() {
    readingMode = mode();
    dimensions = signature();
    anchors = papers.map(capture).filter(Boolean);
    // In document flow only the currently visible paper controls the window.
    const windows = anchors.filter(anchor => anchor.scroll === window);
    if (windows.length > 1) anchors = anchors.filter(anchor => anchor.scroll !== window || anchor === windows.at(-1));
  }
  function restore() {
    restoring = true;
    enableNativeAnchoring(false);
    stableFrames = 0;
    for (const anchor of anchors) {
      if (!anchor.element.isConnected || !anchor.paper.getClientRects().length) continue;
      const scroll = scroller(anchor.paper), view = viewport(anchor.paper, scroll);
      const rect = anchor.range?.startContainer.isConnected ? anchor.range.getBoundingClientRect() : anchor.element.getBoundingClientRect();
      const delta = rect.top - (view.top + anchor.ratio * (view.bottom - view.top));
      if (scroll === window) window.scrollBy({top: delta, behavior: 'instant'});
      else scroll.scrollTop += delta;
      expectedScroll.set(scroll, scroll === window ? window.scrollY : scroll.scrollTop);
    }
    dimensions = signature();
    if (!settleFrame) settleFrame = requestAnimationFrame(settle);
  }
  function settle() {
    settleFrame = 0;
    if (signature() !== dimensions) restore();
    else if (++stableFrames < 2) settleFrame = requestAnimationFrame(settle);
    else { restoring = false; enableNativeAnchoring(true); }
  }
  function layout() {
    if (mode() !== readingMode) {
      cancelAnimationFrame(settleFrame); settleFrame = 0; restoring = false;
      enableNativeAnchoring(true); expectedScroll.clear(); remember();
      return;
    }
    if (signature() !== dimensions && anchors.length) restore();
    else if (!restoring && !anchors.length) remember();
  }
  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; layout(); });
  }
  function scroll(event) {
    const target = event.currentTarget;
    const position = target === window ? window.scrollY : target.scrollTop;
    if (expectedScroll.has(target) && Math.abs(position - expectedScroll.get(target)) < 1) return;
    if (signature() !== dimensions) schedule();
    else {
      if (restoring) {
        cancelAnimationFrame(settleFrame); settleFrame = 0; restoring = false;
        enableNativeAnchoring(true);
      }
      expectedScroll.delete(target);
      remember();
    }
  }
  window.reviewReadingPosition = Object.freeze({isResizing: () => mode() === readingMode && (restoring || signature() !== dimensions)});
  papers.forEach(paper => paper.addEventListener('scroll', scroll, {passive: true}));
  window.addEventListener('scroll', scroll, {passive: true});
  window.addEventListener('resize', schedule, {passive: true});
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(schedule);
    papers.forEach(paper => observer.observe(paper));
  }
  window.addEventListener('load', () => { if (!restoring) remember(); });
  window.addEventListener('review-math-ready', schedule);
  document.addEventListener('review-translations-updated', schedule);
  remember();
  requestAnimationFrame(remember);
})();
