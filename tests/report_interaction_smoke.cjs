const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

class Element {
  constructor(id = '', classes = '', dataset = {}, y = 0, height = 0) {
    this.id = id; this.dataset = dataset; this.y = y; this.height = height;
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
script = script.replace(/\}\)\(\);\s*$/, 'window.hooks={context,filter,nearestAnchor,align};})();');
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
console.log('嵌套折叠、相邻锚点以及仅新增或仅删除的双侧上下文校验通过');
