(() => {
  'use strict';
  const meta = name => document.querySelector(`meta[name="latex-review-annotation-${name}"]`)?.content;
  if (!meta('project')) return;
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
  const button = (text, fn) => { const b = el('button', text); b.type = 'button'; b.onclick = fn; return b; };
  const select = (items) => { const s = el('select'); items.forEach(([v,t]) => { const o=el('option',t); o.value=v; s.append(o); }); return s; };
  const categories = [['question','问题'],['suggestion','建议'],['note','说明']];
  const stable = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
  // Each entry represents one UTF-16 code unit, or one complete formula subtree.
  function coordinates(root) {
    const raw=[];
    function walk(n) {
      if(n.nodeType===3) { for(let i=0;i<n.data.length;i++) raw.push({c:n.data[i],a:[n,i],b:[n,i+1]}); }
      else if(n.nodeType===1 && n.hasAttribute('data-annotation-latex')) {
        const p=n.parentNode,i=Array.prototype.indexOf.call(p.childNodes,n); raw.push({c:'\ufffc',a:[p,i],b:[p,i+1],atom:n});
      } else if(!n.classList?.contains('annotation-card')&&!n.classList?.contains('annotation-mark')) for(const child of n.childNodes) walk(child);
    }
    walk(root);
    const entries=[];
    for(const x of raw) {
      if(/[ \t\r\n\f]/.test(x.c)) {
        if(!entries.length) continue;
        if(entries.at(-1).c===' ') entries.at(-1).b=x.b;
        else entries.push({...x,c:' '});
      } else entries.push(x);
    }
    if(entries.at(-1)?.c===' ') entries.pop();
    function offset(node,index,end=false) {
      const atomIndex=entries.findIndex(e=>e.atom&&(e.atom===node||e.atom.contains(node)));
      if(atomIndex>=0) return atomIndex+(end?1:0);
      const point=document.createRange(); point.setStart(node,index); point.collapse(true);
      for(let i=0;i<entries.length;i++) {
        const begin=document.createRange(); begin.setStart(...entries[i].a); begin.collapse(true);
        if(point.compareBoundaryPoints(Range.START_TO_START,begin)<=0) return i;
        const finish=document.createRange(); finish.setStart(...entries[i].b); finish.collapse(true);
        const relation=point.compareBoundaryPoints(Range.START_TO_START,finish);
        if(relation<0) return i+(end?1:0);
        if(relation===0) return i+1;
      }
      return entries.length;
    }
    return {text:entries.map(x=>x.c).join(''),entries,offset,range(start,end) { const r=document.createRange(); r.setStart(...entries[start].a); r.setEnd(...entries[end-1].b); return r; }};
  }
  let roots=new Map();
  const rootFor=u=>roots.get(u.unit_id||u.id)||document.querySelector(u.selector);
  let content=null,snapshot={threads:[],revision:0},token=null,ready=false,active=null,pending=null,busy=false,adapter=null,readGeneration=0;
  const drafts={};
  const panel=el('details',null,'annotation-overview'); panel.id='annotation-overview'; panel.append(el('summary','批注'));
  const status=el('p','正在读取批注。'); status.id='annotation-status'; status.setAttribute('role','status'); panel.append(status);
  const filters=el('div',null,'annotation-filters');
  const sort=select([['position','正文位置'],['created_at','创建时间'],['updated_at','更新时间']]);
  const identity=select([['','全部作者'],['user','用户'],['agent','智能体']]);
  const category=select([['','全部分类'],...categories]);
  const resolved=select([['','全部状态'],['false','未解决'],['true','已解决']]);
  const location=select([['','全部定位'],['exact','精确定位'],['auto_migrated','自动迁移'],['manual','手动关联'],['needs_relocation','待重新定位']]);
  const search=el('input'); search.type='search'; search.placeholder='搜索批注、作者与原文'; search.setAttribute('aria-label','搜索批注');
  [sort,identity,category,resolved,location].forEach((s,i)=>s.setAttribute('aria-label',['批注排序','作者身份','批注分类','解决状态','定位状态'][i]));
  filters.append(sort,identity,category,resolved,location,search); panel.append(filters);
  const unlocated=el('div'); panel.append(unlocated);
  const list=el('ol'); list.id='annotation-list'; panel.append(button('上一条',()=>step(-1)),button('下一条',()=>step(1)),list);
  (document.querySelector('.reader-tools')||document.querySelector('.live-controls')).after(panel);
  const margin=el('aside',null,'annotation-margin'); margin.setAttribute('aria-label','页边批注');document.querySelector('main').append(margin);
  let relocation=null;
  const layoutCards=[];
  const toolbar=el('div',null,'annotation-toolbar'); toolbar.hidden=true; toolbar.append(button('新建批注',()=>{ toolbar.hidden=true; openComposer('create',null,pending); })); document.body.append(toolbar);
  const editor=el('section',null,'annotation-editor'); editor.hidden=true; editor.setAttribute('aria-label','编辑批注');
  const name=el('input'); name.placeholder='显示名称'; name.value='审阅者'; name.setAttribute('aria-label','显示名称');
  const author=select([['user','用户'],['agent','智能体']]); author.setAttribute('aria-label','作者身份');
  const type=select(categories); type.setAttribute('aria-label','内容分类');
  const body=el('textarea'); body.rows=5; body.setAttribute('aria-label','批注正文');
  let editing=null;
  const formState=()=>({body:body.value,name:name.value,author:author.value,category:type.value});
  const save=button('保存',async()=>{
    if(!editing||busy) return;
    if(editing.warning) {status.textContent=editing.warning;return;}
    if(editing.operation==='create'&&!editing.selection) {status.textContent='请重新选择正文。输入已保留。';return;}
    if(editing.operation==='edit') {const current=snapshot.threads.find(t=>t.id===editing.thread.id)?.messages.find(m=>m.id===editing.message.id);if(!current||current.deleted_at||stable(current)!==stable(editing.message)){status.textContent='操作对象已删除或被其他审阅者编辑。输入已保留。';return;}}
    const submitted=editing,submittedForm=JSON.stringify(formState());
    const req={operation:editing.operation,body:body.value,category:type.value};
    if(editing.thread) req.thread_id=editing.thread.id;
    if(editing.message) req.message_id=editing.message.id;
    if(req.operation!=='edit') req.author={name:name.value,type:author.value};
    if(req.operation==='create') req.selection=editing.selection;
    if(await update(req)) { if(editing===submitted&&JSON.stringify(formState())===submittedForm) {delete drafts[submitted.key]; editor.hidden=true; editing=null;document.dispatchEvent(new CustomEvent('annotation-draft'));} }
  });
  editor.append(el('h3','批注内容'),name,author,type,body,save,button('检查当前操作对象',async()=>{await refresh();if(!editing)return;const t=snapshot.threads.find(t=>t.id===editing.thread?.id),m=t?.messages.find(m=>m.id===editing.message?.id);if(t&&(editing.operation==='reply'||m&&!m.deleted_at)){editing.thread=t;editing.message=m;editing.warning=null;remember();status.textContent='已载入当前对象。请检查输入后保存。';}else status.textContent='对象已删除。输入仍在草稿中。';}),button('收起（保留草稿）',()=>{editor.hidden=true;})); document.body.append(editor);
  function remember(notify=true) { if(editing) drafts[editing.key]={...editing,body:body.value,name:name.value,author:author.value,category:type.value}; if(notify) document.dispatchEvent(new CustomEvent('annotation-draft')); }
  [body,name,author,type].forEach(e=>e.addEventListener('input',remember));
  function openComposer(operation,thread,selection,message) {
    remember(); const key=operation+':'+(message?.id||thread?.id||JSON.stringify(selection));
    editing={operation,thread,selection,message,key}; const d=drafts[key];
    if(d) editing={...d,operation,thread,selection,message,key};
    if(operation==='create'&&content&&selection) {editing.content=structuredClone(content);try{editing.anchor=window.reviewAnnotationDelivery?.anchor(content,selection);}catch(_){}}
    body.value=d?.body??message?.body??''; type.value=d?.category??message?.category??'question';
    if(d) {name.value=d.name;author.value=d.author;} name.hidden=author.hidden=operation==='edit';
    editor.hidden=false; if(editing.warning) status.textContent=editing.warning;body.focus();
  }
  async function json(url,options) { const r=await fetch(url,options); const data=await r.json(); if(!r.ok) throw Error(data.message||data.error||`请求失败 ${r.status}`); return data; }
  function check(c,s) { if(c.project_id!==meta('project')||c.version_id!==meta('version')||s.project_id!==c.project_id||s.version_id!==c.version_id) throw Error('正文版本已变化。禁止写入；输入已保留。'); }
  async function refresh() {
    const generation=++readGeneration; ready=false;
    try {
      const [c,s]=adapter?await adapter.read():await Promise.all([json('/api/annotations/content'),json('/api/annotations/status')]);
      if(generation!==readGeneration) return;
      check(c,s); document.querySelectorAll('.annotation-card,.annotation-mark').forEach(e=>e.remove()); content=c; roots=new Map(c.units.map(u=>[u.id,document.querySelector(u.selector)])); snapshot=s; token=s.token||token; ready=true; render(); status.textContent=`${s.threads.length} 条批注`;
    } catch(e) {if(generation!==readGeneration) return; status.textContent=window.location.protocol==='file:'?'批注需要本机预览接口。请使用 latex-review-preview 打开报告。':e.message;}
  }
  async function update(request) {
    if(!ready||busy) {status.textContent='批注接口或版本不可用。输入已保留。'; return false;}
    remember(); busy=true; save.disabled=true;
    try {
      check(content,snapshot); ++readGeneration;
      const envelope={project_id:content.project_id,version_id:content.version_id,request:{...request,expected_revision:snapshot.revision}};
      const s=adapter?await adapter.update(envelope):await json('/api/annotations/update',{method:'POST',headers:{'Content-Type':'application/json','X-LaTeX-Review-Token':token},body:JSON.stringify(envelope)});
      check(content,s); snapshot=s; render(); status.textContent='批注已保存。'; return true;
    } catch(e) { await refresh(); status.textContent=e.message+' 输入已保留。请检查后重新保存。'; return false; }
    finally {busy=false;save.disabled=false;}
  }
  function filtered() {
    const q=search.value.toLowerCase();
    return snapshot.threads.filter(t=>{
      const m=t.messages.filter(x=>!x.deleted_at),a=t.association;
      return (!identity.value||m.some(x=>x.author.type===identity.value))&&(!category.value||m.some(x=>x.category===category.value))&&(!resolved.value||String(t.resolved)===resolved.value)&&(!location.value||a.status===location.value)&&(!q||[t.original_anchor.quote,a.anchor?.quote,...m.flatMap(x=>[x.body,x.author.name])].join(' ').toLowerCase().includes(q));
    }).sort((a,b)=>sort.value==='position'?((a.association.anchor?.order??1e12)-(b.association.anchor?.order??1e12)||(a.association.anchor?.start??0)-(b.association.anchor?.start??0)||a.created_at.localeCompare(b.created_at)):a[sort.value].localeCompare(b[sort.value]));
  }
  function step(delta) {const ts=filtered(); if(ts.length) focus(ts[(ts.findIndex(t=>t.id===active)+delta+ts.length)%ts.length]);}
  function focus(t) {
    active=t.id; const a=t.association.anchor;
    if(a&&!window.reviewNavigation) rootFor(a)?.scrollIntoView({block:'center'});
    if(a) {window.reviewNavigation?.node('new',a.node_id); if(a.change_ids?.length) window.reviewNavigation?.annotation(a.change_ids[0]);}
    const card=document.querySelector(`[data-annotation-thread="${CSS.escape(t.id)}"]`); if(card) {card.open=true;card.scrollIntoView({block:'nearest'});}
  }
  const pendingMath=new Map();
  window.addEventListener('review-math-ready',()=>{
    for(const [formula,renderFormula] of pendingMath) {
      if(formula.isConnected) renderFormula(); else pendingMath.delete(formula);
    }
  });
  function safeMath(target,text) {
    const source=el('div',null,'annotation-body'); target.append(source);
    const pattern=/\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]|\$([^$\n]+)\$|\\\(([\s\S]*?)\\\)/g;
    let cursor=0;
    for(const match of text.matchAll(pattern)) {
      source.append(document.createTextNode(text.slice(cursor,match.index)));
      const formula=el('span',match[0],'annotation-formula'); source.append(formula);
      const latex=match[1]??match[2]??match[3]??match[4],display=match[1]!=null||match[2]!=null;
      let rendering=false;
      const renderFormula=async()=>{
        if(rendering) return;
        rendering=true;
        try {
          await window.MathJax?.startup?.promise;
          if(window.MathJax?.tex2chtmlPromise) {
            const rendered=await window.MathJax.tex2chtmlPromise(latex,{display});
            if(rendered.querySelector('mjx-merror,[data-mjx-error]')) throw Error('公式排版失败');
            for(const node of [rendered,...rendered.querySelectorAll('*')]) {
              for(const attr of [...node.attributes]) {
                if(/^on/i.test(attr.name)||(['href','xlink:href','src'].includes(attr.name)&&/^(javascript|data|vbscript):/i.test(attr.value.replace(/[\x00-\x20]/g,'')))) node.removeAttribute(attr.name);
              }
            }
            if(formula.isConnected) {formula.replaceChildren(rendered);document.dispatchEvent(new CustomEvent('annotation-layout'));}
            pendingMath.delete(formula);
          }
        } catch(e) {formula.textContent=match[0];pendingMath.delete(formula);}
        finally {rendering=false;}
      };
      pendingMath.set(formula,renderFormula);
      queueMicrotask(renderFormula);
      cursor=match.index+match[0].length;
    }
    source.append(document.createTextNode(text.slice(cursor)));
  }
  function card(t) {
    const d=el('details',null,'annotation-card');d.open=true; d.dataset.annotationThread=t.id;
    d.append(el('summary',`${t.resolved?'已解决':'未解决'} · ${t.messages.find(m=>!m.deleted_at)?.author.name||'批注'}`));
    d.append(el('p','创建时原文：'+(t.original_anchor.quote||t.original_anchor.raw_latex)));
    const a=t.association.anchor; d.append(el('p','当前内容：'+(a?.quote||a?.raw_latex||'待重新定位')));
    const history=el('details');history.append(el('summary','历次关联快照'));t.history.forEach(h=>history.append(el('p',h.at+' · '+h.method+'：'+(h.anchor.quote||h.anchor.raw_latex))));d.append(history);
    if(t.association.status==='needs_relocation') {
      d.append(el('p','待重新定位'));
      t.association.candidates.forEach(candidate=>d.append(el('p','候选：'+candidate.anchor.section_path.join(' / ')+' · '+(candidate.anchor.unit_text||candidate.anchor.raw_latex)+' · '+candidate.score)));
      d.append(button('手动重新关联',()=>{relocation=t;status.textContent='请选择当前段落内文字或完整公式。';}));
    }
    if(t.association.status==='auto_migrated') d.append(el('p','自动迁移'));
    t.messages.filter(m=>!m.deleted_at).forEach(m=>{ const row=el('div',null,'annotation-message'); row.append(el('strong',`${m.author.name} · ${m.author.type==='agent'?'智能体':'用户'} · ${categories.find(x=>x[0]===m.category)?.[1]}`));safeMath(row,m.body);row.append(button('编辑',()=>openComposer('edit',t,null,m)),button('删除内容',()=>update({operation:'delete_message',thread_id:t.id,message_id:m.id}))); d.append(row); });
    d.append(button('回复',()=>openComposer('reply',t)),button(t.resolved?'重新打开':'解决',()=>update({operation:t.resolved?'reopen':'resolve',thread_id:t.id})),button('删除线程',()=>update({operation:'delete_thread',thread_id:t.id})),button('定位正文',()=>focus(t)));
    return d;
  }
  function render() {
    document.querySelectorAll('.annotation-card,.annotation-mark').forEach(e=>e.remove());
    for(const formula of pendingMath.keys()) if(!formula.isConnected) pendingMath.delete(formula);
    list.replaceChildren(); unlocated.replaceChildren();margin.replaceChildren();layoutCards.length=0;
    const highlights=[];
    for(const t of filtered()) { const li=el('li');li.append(button(`${t.resolved?'已解决':'未解决'} · ${t.messages.find(m=>!m.deleted_at)?.body||'批注'}`,()=>focus(t)));list.append(li); }
    for(const t of snapshot.threads) {
      const a=t.association.anchor; if(!a) {const c=card(t);margin.append(c);layoutCards.push({card:c,root:null});continue;}
      const root=rootFor(a); if(!root) {const c=card(t);margin.append(c);layoutCards.push({card:c,root:null});continue;}
      const c=card(t),change=a.change_ids?.[0]; const host=change?document.getElementById('inline-'+change):null;
      const prose=content.units.find(u=>u.kind==='text'&&rootFor(u)?.contains(root));
      const attachment=prose?rootFor(prose):(a.kind==='formula'?root.parentElement:root);
      if(host) host.append(c); else {margin.append(c);layoutCards.push({card:c,root:attachment});}
      const mark=button('批注',()=>overlap(a)); mark.className='annotation-mark';mark.dataset.unit=a.unit_id;attachment.append(mark);
      if(a.kind==='text') {const map=coordinates(root);if(map.text===a.unit_text&&a.end<=map.entries.length) highlights.push(map.range(a.start,a.end));}
    }
    if(window.CSS?.highlights&&window.Highlight) CSS.highlights.set('annotations',new Highlight(...highlights));
    document.body.classList.toggle('annotation-margin-active',layoutCards.length>0);arrange();document.dispatchEvent(new CustomEvent('annotation-layout'));
  }
  function arrange() {
    const paper=document.querySelector('.preview-side[data-side="new"]')||document.querySelector('.preview-side');if(!paper)return;
    const desktop=innerWidth>=800&&!document.body.classList.contains('compare-view');
    margin.classList.toggle('annotation-margin-desktop',desktop);
    if(!desktop){if(!window.reviewNavigation)document.querySelector('main').style.setProperty('--margin-overflow','0px');margin.style.left='';margin.style.width='';margin.style.minHeight='';layoutCards.forEach(({card})=>{card.style.position='';card.style.top='';});return;}
    const rect=paper.getBoundingClientRect(),main=document.querySelector('main').getBoundingClientRect();
    margin.style.left=(rect.right-main.left+24)+'px';margin.style.width=Math.min(520,innerWidth-rect.right-40)+'px';
    let bottom=rect.top;
    layoutCards.forEach(({card,root})=>{const top=Math.max(root?.getBoundingClientRect().top||rect.top,bottom+12);card.style.position='absolute';card.style.top=(top-main.top)+'px';bottom=top+card.getBoundingClientRect().height;});
    margin.style.minHeight=Math.max(0,bottom-main.top)+'px';
    if(!window.reviewNavigation)document.querySelector('main').style.setProperty('--margin-overflow',Math.max(0,bottom-rect.bottom)+'px');
  }
  window.reviewAnnotationMargin={entries:()=>layoutCards,arrange};
  window.addEventListener('resize',arrange);window.addEventListener('review-math-ready',arrange);document.querySelector('main').addEventListener('load',arrange,true);if(document.fonts)document.fonts.ready.then(arrange);document.addEventListener('annotation-layout',()=>{if(!window.reviewNavigation)requestAnimationFrame(arrange);});
  margin.addEventListener('toggle',()=>{arrange();document.dispatchEvent(new CustomEvent('annotation-layout'));},true);
  function overlap(a) {
    panel.open=true;list.replaceChildren(); snapshot.threads.filter(t=>{const b=t.association.anchor;return b?.unit_id===a.unit_id&&(a.kind==='formula'||b.start<a.end&&b.end>a.start);}).forEach(t=>{const li=el('li');li.append(button(t.messages.find(m=>!m.deleted_at)?.body||'批注',()=>focus(t)));list.append(li);});
  }
  function selection() {
    const s=getSelection(); if(!s.rangeCount||s.isCollapsed) return null;
    const r=s.getRangeAt(0);
    if([r.startContainer,r.endContainer].some(n=>(n.nodeType===1?n:n.parentElement)?.closest('.annotation-card,.annotation-mark'))) throw Error('请选择正文。');
    const contains=(root,n)=>root===n||root.contains(n);
    const fs=content.units.filter(u=>u.kind==='formula');
    for(const u of fs) {const root=rootFor(u);if(root&&contains(root,r.startContainer)&&contains(root,r.endContainer)) return {unit_id:u.id,side:'new'};}
    for(const u of content.units.filter(u=>u.kind==='text')) {
      const root=rootFor(u);if(!root||!contains(root,r.startContainer)||!contains(root,r.endContainer)) continue;
      const map=coordinates(root); if(map.text!==u.text) throw Error('正文坐标不一致。禁止新建批注。');
      const start=map.offset(r.startContainer,r.startOffset),end=map.offset(r.endContainer,r.endOffset,true);
      if(/[\uD800-\uDBFF]/.test(map.text[start-1]||'')||/[\uDC00-\uDFFF]/.test(map.text[end]||'')) throw Error('选区不能切分 Unicode 字符。');
      if(end>start) return {unit_id:u.id,start,end,quote:map.text.slice(start,end),side:'new'};
    }
    throw Error('只能选择修改后版本的单个段落或完整公式。');
  }
  document.addEventListener('mouseup',e=>{
    if(panel.contains(e.target)||editor.contains(e.target)||toolbar.contains(e.target)) return;
    toolbar.hidden=true;if(!ready) return;
    try { pending=selection(); if(pending&&relocation) {const t=relocation;relocation=null;update({operation:'associate',thread_id:t.id,selection:pending});return;}
      if(pending&&editing?.operation==='create'&&!editing.selection){editing.selection=pending;editing.content=structuredClone(content);editing.anchor=window.reviewAnnotationDelivery?.anchor(content,pending);editing.warning=null;remember();status.textContent='草稿已关联到当前选区。';return;}
      if(pending) {toolbar.hidden=false;const rect=getSelection().getRangeAt(0).getBoundingClientRect();toolbar.style.top=Math.max(4,rect.bottom+6)+'px';toolbar.style.left=Math.min(innerWidth-130,Math.max(4,rect.left))+'px';} } catch(e) {status.textContent=e.message;}
  });
  document.addEventListener('click',e=>{
    if(!ready||e.target.closest('.annotation-card,.annotation-mark')) return;
    const point=document.caretRangeFromPoint?.(e.clientX,e.clientY);
    if(point&&getSelection().isCollapsed) {
      for(const u of content.units.filter(u=>u.kind==='text')) {const root=rootFor(u);if(root?.contains(point.startContainer)){const map=coordinates(root),offset=map.offset(point.startContainer,point.startOffset);const t=snapshot.threads.find(t=>{const a=t.association.anchor;return a?.unit_id===u.id&&a.start<=offset&&a.end>offset;});if(t) overlap({...t.association.anchor,start:offset,end:offset+1});break;}}
    }
    const formula=e.target.closest('[data-annotation-latex]'); if(formula) {const u=content.units.find(u=>u.kind==='formula'&&rootFor(u)===formula);if(u){pending={unit_id:u.id,side:'new'};if(relocation){const t=relocation;relocation=null;update({operation:'associate',thread_id:t.id,selection:pending});return;}if(editing?.operation==='create'&&!editing.selection){editing.selection=pending;editing.content=structuredClone(content);editing.anchor=window.reviewAnnotationDelivery?.anchor(content,pending);editing.warning=null;remember();return;}toolbar.hidden=false;const r=formula.getBoundingClientRect();toolbar.style.top=r.bottom+'px';toolbar.style.left=Math.min(innerWidth-130,r.left)+'px';}}
  });
  [sort,identity,category,resolved,location,search].forEach(e=>e.addEventListener('input',render));
  window.reviewAnnotations=Object.freeze({coordinates,selection,refresh,update,openComposer,focus,
    getDrafts(){remember(false);return structuredClone(drafts);},restoreDrafts(value){Object.assign(drafts,value);const tray=el('div',null,'annotation-drafts');tray.append(el('p','保留的编辑草稿'));Object.entries(value).forEach(([key,d])=>tray.append(button(d.body.slice(0,60)||'空草稿',()=>window.reviewAnnotations.resumeDraft(key))));panel.append(tray);},resumeDraft(key){const d=drafts[key];if(d){openComposer(d.operation,d.thread,d.selection,d.message);editing={...d,key};body.value=d.body;name.value=d.name;author.value=d.author;type.value=d.category;if(d.warning)status.textContent=d.warning;}},
    setAdapter(value){adapter=value;return refresh();},getContent(){return content;},getSnapshot(){return snapshot;}});
  if(!document.getElementById('annotation-content')&&window.location.protocol!=='file:') refresh();
})();
