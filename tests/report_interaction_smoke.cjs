const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

class Element {
  constructor(id = '', classes = '', dataset = {}, y = 0, height = 0) {
    this.id = id; this.dataset = dataset; this.y = y; this.height = height;
    this.tagName = 'DIV';
    this.offsetHeight = height;
    this.parentElement = null; this.children = []; this.scrollTop = 0; this.clientHeight = 500;
    this.hidden = false; this.value = 'all'; this.events = {};
    this.classes = new Set(classes.split(' ').filter(Boolean));
    this.classList = {
      contains: name => this.classes.has(name),
      add: name => this.classes.add(name),
      remove: name => this.classes.delete(name),
      toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
    };
  }
  append(child) { child.parentElement = this; this.children.push(child); return child; }
  insertBefore(child) { return this.append(child); }
  setAttribute() {}
  addEventListener(name, handler) { this.events[name] = handler; }
  contains(other) { for (let at = other; at; at = at.parentElement) if (at === this) return true; return false; }
  closest(selector) { for (let at = this; at; at = at.parentElement) if (at.matches(selector)) return at; return null; }
  matches(selector) {
    if (selector === '.review-node') return this.classes.has('review-node');
    if (selector === '.change-card') return this.classes.has('change-card');
    if (selector === '.change-jump') return this.classes.has('change-jump');
    if (selector === '.side-empty') return this.classes.has('side-empty');
    if (selector === '.preview-side .is-highlighted') return this.classes.has('is-highlighted');
    if (selector === '.preview-side .is-cell-highlighted') return this.classes.has('is-cell-highlighted');
    if (selector === 'button[data-old][data-new]') return this.dataset.old !== undefined && this.dataset.new !== undefined;
    if (selector === 'td[data-row][data-column]') {
      return this.tagName === 'TD' && this.dataset.row !== undefined && this.dataset.column !== undefined;
    }
    if (selector === 'h2') return this.id.endsWith('-heading');
    return false;
  }
  querySelectorAll(selector) {
    const found = [];
    const visit = element => { for (const child of element.children) {
      if (child.matches(selector)) found.push(child);
      visit(child);
    }};
    visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  panel() { for (let at = this; at; at = at.parentElement) if (at.classes.has('preview-side')) return at; return this; }
  getBoundingClientRect() {
    const top = this.classes.has('preview-side') ? 0 : this.y - this.panel().scrollTop;
    const height = this.classes.has('preview-side') ? this.clientHeight : this.height;
    return {top, bottom: top + height, height};
  }
  getClientRects() {
    for (let at = this; at; at = at.parentElement) if (at.classes.has('context-hidden')) return [];
    return [this.getBoundingClientRect()];
  }
}

const ids = new Map();
const register = element => { ids.set(element.id, element); return element; };
const root = new Element('root');
const old = register(root.append(new Element('old-panel', 'preview-side', {side:'old'}, 0, 1200)));
const fresh = register(root.append(new Element('new-panel', 'preview-side', {side:'new'}, 0, 1300)));
old.append(new Element('old-heading'));
fresh.append(new Element('new-heading'));
const oldSection = register(old.append(new Element('old-section-old', 'review-node', {nodeId:'section-old'}, 0, 1200)));
const newSection = register(fresh.append(new Element('new-section-new', 'review-node', {nodeId:'section-new'}, 0, 1300)));
const oldParagraphs = [], newParagraphs = [];
for (let i = 1; i <= 6; i++) {
  oldParagraphs.push(register(oldSection.append(new Element('old-p'+i+'-old', 'review-node', {nodeId:'p'+i+'-old'}, 100 + (i-1)*150, 100))));
  newParagraphs.push(register(newSection.append(new Element('new-p'+i+'-new', 'review-node', {nodeId:'p'+i+'-new'}, 100 + (i-1)*150 + (i>=3 ? 150 : 0), 100))));
}
const inserted = register(newSection.append(new Element('new-inserted', 'review-node', {nodeId:'inserted'}, 400, 100)));
newSection.children.splice(newSection.children.indexOf(inserted), 1);
newSection.children.splice(2, 0, inserted);
const removed = register(oldSection.append(new Element('old-removed', 'review-node', {nodeId:'removed'}, 375, 50)));
oldSection.children.splice(oldSection.children.indexOf(removed), 1);
oldSection.children.splice(2, 0, removed);
const changes = register(root.append(new Element('changes-side')));
const card = changes.append(new Element('change-1', 'change-card', {kind:'modified',categories:'text'}));
card.append(new Element('jump-1', 'change-jump', {old:'p4-old',new:'p4-new'}));
for (const id of ['kind-filter','category-filter','reading-mode','sync-scroll','filter-count','jump-status','previous-change','next-change']) register(root.append(new Element(id)));
const document = {
  querySelectorAll: selector => selector === '.change-card' ? [card] : [],
  querySelector: selector => selector.includes('old') ? old : fresh,
  getElementById: id => ids.get(id),
  createElement: () => new Element('', 'side-empty'),
  addEventListener() {},
};
const window = {reviewNodePairs: [['section-old','section-new'], ...Array.from({length:6}, (_,i)=>['p'+(i+1)+'-old','p'+(i+1)+'-new'])], matchMedia:()=>({matches:false})};
let script = fs.readFileSync(process.argv[2], 'utf8');
script = script.replace(/\}\)\(\);\s*$/, 'window.hooks={context,filter,nearestAnchor,align,jump};})();');
let now = 1000;
vm.runInNewContext(script, {document, window, navigator:{}, performance:{now:()=>now}, requestAnimationFrame: fn=>fn(), console});

ids.get('reading-mode').value = 'context';
window.hooks.context();
assert(!oldSection.classes.has('context-hidden'));
assert(oldParagraphs[0].classes.has('context-hidden'));
assert(oldParagraphs[1].classes.has('context-hidden'));
assert(!oldParagraphs[2].classes.has('context-hidden'));
assert(!oldParagraphs[3].classes.has('context-hidden'));
assert(!oldParagraphs[4].classes.has('context-hidden'));
assert(oldParagraphs[5].classes.has('context-hidden'));

ids.get('reading-mode').value = 'full';
window.hooks.context();
fresh.scrollTop = 250;
const anchor = window.hooks.nearestAnchor('new', inserted, 250);
assert(anchor[0] === newParagraphs[2], '新增段落应选新侧相邻段落');
assert(anchor[1] === oldParagraphs[2], '锚点应指向旧侧对应段落');
ids.get('sync-scroll').checked = true;
window.hooks.align('new');
assert(old.scrollTop > 100 && old.scrollTop < 350, 'old scrollTop=' + old.scrollTop);

const jump = card.querySelector('.change-jump');
card.dataset.kind = 'added';
jump.dataset.old = ''; jump.dataset.new = 'inserted';
ids.get('kind-filter').value = 'added';
ids.get('reading-mode').value = 'context';
window.hooks.filter();
assert(oldParagraphs.some(node => node.getClientRects().length), '仅新增时旧侧不可空白');
const addedAnchor = window.hooks.nearestAnchor('new', inserted, 250);
assert(addedAnchor && addedAnchor[1].getClientRects().length, '仅新增时仍需可见旧侧锚点');
now = 1500;
window.hooks.align('new');

card.dataset.kind = 'removed';
jump.dataset.old = 'removed'; jump.dataset.new = '';
ids.get('kind-filter').value = 'removed';
window.hooks.filter();
assert(newParagraphs.some(node => node.getClientRects().length), '仅删除时新侧不可空白');
const removedAnchor = window.hooks.nearestAnchor('old', removed, 250);
assert(removedAnchor && removedAnchor[1].getClientRects().length, '仅删除时仍需可见新侧锚点');
now = 2000;
window.hooks.align('old');

const oldTable = register(oldSection.append(new Element('old-table-old', 'review-node', {nodeId:'table-old'}, 900, 100)));
const newTable = register(newSection.append(new Element('new-table-new', 'review-node', {nodeId:'table-new'}, 900, 100)));
const oldOtherCell = oldTable.append(new Element('', '', {row:'1',column:'1'}));
oldOtherCell.tagName = 'TD';
const oldTargetCell = oldTable.append(new Element('', '', {row:'1',column:'2'}));
oldTargetCell.tagName = 'TD';
const newOtherCell = newTable.append(new Element('', '', {row:'1',column:'2'}));
newOtherCell.tagName = 'TD';
const newTargetCell = newTable.append(new Element('', '', {row:'1',column:'3'}));
newTargetCell.tagName = 'TD';
const detailJump = card.append(new Element('table-detail', 'detail-jump', {
  old:'table-old', new:'table-new', oldLocation:'旧表格', newLocation:'新表格',
  oldRow:'1', oldColumn:'2', newRow:'1', newColumn:'3'
}));
changes.events.click({target:detailJump});
assert(oldTargetCell.classes.has('is-cell-highlighted'), '旧侧应定位到对应单元格');
assert(!oldOtherCell.classes.has('is-cell-highlighted'), '旧侧其他单元格不应高亮');
assert(newTargetCell.classes.has('is-cell-highlighted'), '新侧应定位到对应单元格');
assert(!newOtherCell.classes.has('is-cell-highlighted'), '新侧其他单元格不应高亮');

const oldSentence = register(oldParagraphs[3].append(new Element('old-p4-old-sentence-2', 'review-sentence', {}, 600, 20)));
const newSentence = register(newParagraphs[3].append(new Element('new-p4-new-sentence-2', 'review-sentence', {}, 750, 20)));
const sentenceJump = card.append(new Element('sentence-detail', 'detail-jump', {
  old:'p4-old', new:'p4-new', oldSentences:'p4-old-sentence-2', newSentences:'p4-new-sentence-2',
  oldLocation:'旧段落', newLocation:'新段落'
}));
window.hooks.jump(sentenceJump);
assert(oldSentence.classes.has('is-highlighted') && newSentence.classes.has('is-highlighted'), '应高亮双侧对应句子');
assert(!oldParagraphs[3].classes.has('is-highlighted') && !newParagraphs[3].classes.has('is-highlighted'), '不应把整段当作句子定位');
assert(ids.get('jump-status').textContent.includes('已定位完整句子'), '应报告精确句子定位');

sentenceJump.dataset.oldSentences = 'p4-old-sentence-missing';
window.hooks.jump(sentenceJump);
assert(!oldParagraphs[3].classes.has('is-highlighted'), '无法定位时不可回退高亮整段');
assert(ids.get('jump-status').textContent.includes('句子定位不可确认'), '应报告定位不确定');

console.log('上下文、双侧滚动、表格与句子定位校验通过');
