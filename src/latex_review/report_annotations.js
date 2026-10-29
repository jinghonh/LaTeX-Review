(() => {
  'use strict';
  const readonly = document.querySelector('meta[name="latex-review-readonly"]')?.content === 'snapshot';
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
      } else if(!n.classList?.contains('annotation-card')&&!n.classList?.contains('annotation-mark')&&!n.classList?.contains('structure-number')) for(const child of n.childNodes) walk(child);
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
  let content=null,snapshot={threads:[],revision:0},ready=false,active=null,pending=null,busy=false,adapter=null,readGeneration=0;
  const drafts={};
  const panel=el('details',null,'annotation-overview'); panel.id='annotation-overview'; panel.append(el('summary','批注'));
  let scope=null;
  const heading=el('div',null,'annotation-panel-heading');
  heading.append(el('h2','批注'),button('关闭',()=>{remember();panel.open=false;}));panel.append(heading);
  const all=button('全部批注',()=>{scope=null;render();});panel.append(all);
  panel.addEventListener('toggle',()=>{if(!panel.open)active=null;paintActive();document.body.classList.toggle('annotation-panel-open',panel.open);document.dispatchEvent(new CustomEvent('annotation-layout'));});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&panel.open){remember();panel.open=false;}});
  const status=el('p','正在读取批注。'); status.id='annotation-status'; status.setAttribute('role','status'); panel.append(status);
  const filters=el('div',null,'annotation-filters');
  const sort=select([['position','正文位置'],['created_at','创建时间'],['updated_at','更新时间']]);
  const identity=select([['','全部作者'],['user','用户'],['agent','智能体']]);
  const category=select([['','全部分类'],...categories]);
  const resolved=select([['','全部状态'],['false','未解决'],['true','已解决']]);
  const location=select([['','全部定位'],['exact','精确定位'],['auto_migrated','自动迁移'],['manual','手动关联'],['needs_relocation','待重新定位']]);
  const search=el('input'); search.type='search'; search.placeholder='搜索批注、作者与原文'; search.setAttribute('aria-label','搜索批注');
  [sort,identity,category,resolved,location].forEach((s,i)=>s.setAttribute('aria-label',['批注排序','作者身份','批注分类','解决状态','定位状态'][i]));
  resolved.value='false';
  const filterDetails=el('details',null,'annotation-filter-options');filterDetails.append(el('summary','筛选与排序'),filters);
  filters.append(sort,identity,category,resolved,location);panel.append(search,filterDetails);
  const unlocated=el('div',null,'annotation-discussions'); panel.append(unlocated);
  const list=el('ol'); list.id='annotation-list';
  const navigation=el('div',null,'annotation-navigation'),position=el('span');
  navigation.append(button('上一条',()=>step(-1)),position,button('下一条',()=>step(1)));panel.append(list,navigation);
  const header=el('div',null,'annotation-panel-header');header.append(heading,all,status,search,filterDetails);panel.insertBefore(header,unlocated);
  (document.querySelector('.reader-tools')||document.querySelector('.live-controls')).after(panel);
  const margin=el('aside',null,'annotation-margin'); margin.setAttribute('aria-label','页边批注');document.querySelector('main').append(margin);
  let relocation=null;
  const layoutCards=[];
  const toolbar=el('div',null,'annotation-toolbar'); toolbar.hidden=true; toolbar.append(button('批注',()=>{ toolbar.hidden=true; openComposer('create',null,pending); })); document.body.append(toolbar);
  const editor=el('section',null,'annotation-editor'); editor.hidden=true; editor.setAttribute('aria-label','编辑批注');
  const name=el('input'); name.placeholder='显示名称'; name.value='审阅者';try{if(!readonly)name.value=localStorage.getItem('latex-review-author-name')||name.value;}catch(_){} name.setAttribute('aria-label','显示名称');
  const author=select([['user','用户'],['agent','智能体']]); author.setAttribute('aria-label','作者身份');
  const type=select(categories); type.setAttribute('aria-label','内容分类');
  const body=el('textarea'); body.rows=5; body.setAttribute('aria-label','批注正文');
  const original=el('textarea'); original.setAttribute('aria-label','原始 LaTeX 块');
  const replacement=el('textarea'); replacement.setAttribute('aria-label','替换 LaTeX 块');
  const suggestionFields=el('details',null,'annotation-suggestion-editor'); suggestionFields.append(el('summary','源码替换建议'),original,replacement);
  let editing=null;
  const formState=()=>({body:body.value,name:name.value,author:author.value,category:type.value,original:original.value,replacement:replacement.value});
  const save=button('保存',async()=>{
    if(!editing||busy) return;
    if(editing.warning) {status.textContent=editing.warning;recheck.hidden=false;return;}
    if(editing.operation==='create'&&!editing.selection) {status.textContent='请重新选择正文。输入已保留。';return;}
    if(editing.operation==='edit') {const current=snapshot.threads.find(t=>t.id===editing.thread.id)?.messages.find(m=>m.id===editing.message.id);if(!current||current.deleted_at||stable(current)!==stable(editing.message)){status.textContent='操作对象已删除或被其他审阅者编辑。输入已保留。';return;}}
    if(editing.operation==='reply'&&editing.message&&!snapshot.threads.find(t=>t.id===editing.thread.id)?.messages.some(m=>m.id===editing.message.id&&!m.deleted_at)){status.textContent='回复对象已删除。输入已保留。';return;}
    const submitted=editing,submittedForm=JSON.stringify(formState());
    const req={operation:editing.operation,body:body.value,category:type.value};
    if(editing.thread) req.thread_id=editing.thread.id;
    if(editing.message&&editing.operation==='edit') req.message_id=editing.message.id;
    if(editing.message&&editing.operation==='reply') req.reply_to=editing.message.id;
    if(req.operation!=='edit') req.author={name:name.value,type:author.value};
    if(req.operation==='create') req.selection=editing.selection;
    if(req.operation!=='edit'&&original.value) req.suggested_replacement={original_chunk:original.value,replacement_chunk:replacement.value};
    if(await update(req)) { if(editing===submitted&&JSON.stringify(formState())===submittedForm) {delete drafts[submitted.key];try{localStorage.setItem('latex-review-author-name',name.value);}catch(_){} editor.hidden=true; editing=null;document.dispatchEvent(new CustomEvent('annotation-draft'));} }
  });
  const settings=el('details',null,'annotation-composer-settings');settings.append(el('summary','作者与分类'),name,author,type);
  body.placeholder='写下你的修改意见…';
  const recheck=button('检查当前操作对象',async()=>{await refresh();if(!editing)return;const t=snapshot.threads.find(t=>t.id===editing.thread?.id),m=t?.messages.find(m=>m.id===editing.message?.id);if(t&&(editing.operation==='reply'&&!editing.message||m&&!m.deleted_at)){editing.thread=t;editing.message=m;editing.warning=null;remember();status.textContent='已载入当前对象。请检查输入后保存。';}else status.textContent='对象已删除。输入仍在草稿中。';});recheck.hidden=true;
  editor.append(el('h3','批注内容'),body,suggestionFields,settings,save,recheck,button('收起（保留草稿）',()=>{remember();editor.hidden=true;}));panel.append(editor);
  function remember(notify=true) { if(editing) drafts[editing.key]={...editing,body:body.value,name:name.value,author:author.value,category:type.value,original:original.value,replacement:replacement.value}; if(notify&&editing) document.dispatchEvent(new CustomEvent('annotation-draft')); }
  [body,name,author,type,original,replacement].forEach(e=>e.addEventListener('input',remember));
  function openComposer(operation,thread,selection,message) {
    if(readonly)return;
    remember(); const key=operation+':'+(message?.id||thread?.id||JSON.stringify(selection));
    editing={operation,thread,selection,message,key}; const d=drafts[key];
    if(d) editing={...d,operation,thread,selection,message,key};
    if(operation==='create'&&content&&selection) {editing.content=structuredClone(content);try{editing.anchor=window.reviewAnnotationDelivery?.anchor(content,selection);}catch(_){}}
    suggestionFields.hidden=operation==='edit'; original.value=d?.original??''; replacement.value=d?.replacement??'';
    body.value=d?.body??(operation==='reply'?'':message?.body??''); type.value=d?.category??(operation==='edit'?message?.category:null)??'question';
    editor.querySelector('h3').textContent=operation==='reply'&&message?`回复 ${message.author.name}`:'批注内容';
    if(d) {name.value=d.name;author.value=d.author;} name.hidden=author.hidden=operation==='edit';
    panel.open=true;recheck.hidden=!editing.warning;editor.hidden=false; if(editing.warning) status.textContent=editing.warning;body.focus();
  }
  function check(c,s) { if(c.project_id!==meta('project')||c.version_id!==meta('version')||s.project_id!==c.project_id||s.version_id!==c.version_id) throw Error('正文版本已变化。禁止写入；输入已保留。'); }
  let preparation=null;
  function refresh() {
    preparation=readAndRender();return preparation;
  }
  async function readAndRender() {
    const generation=++readGeneration; ready=false;
    try {
      const [c,s]=await adapter.read();
      if(generation!==readGeneration) return;
      check(c,s); document.querySelectorAll('.annotation-card,.annotation-mark').forEach(e=>e.remove()); content=c; roots=new Map(c.units.map(u=>[u.id,document.querySelector(u.selector)])); snapshot=s; render(); status.textContent=`${s.threads.length} 条批注`;
      await document.fonts?.ready;
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      if(generation===readGeneration)ready=true;
    } catch(e) {if(generation!==readGeneration) return; status.textContent=window.location.protocol==='file:'?'批注需要本机预览接口。请使用 latex-review-preview 打开报告。':e.message;}
  }
  let polling=false;
  async function pollUpdates() {
    if(readonly||polling||busy||!ready||!content||document.hidden)return;
    polling=true;const generation=readGeneration;
    try {
      let s=await adapter.readRevision();
      if(generation!==readGeneration||busy)return;
      check(content,s);
      if(s.revision===snapshot.revision)return;
      s=await adapter.readChanges(snapshot.revision);
      if(generation!==readGeneration||busy)return;
      check(content,s);remember(false);
      const scroll=unlocated.scrollTop,changed=acceptSnapshot(s);
      render(changed);unlocated.scrollTop=scroll;
      status.textContent='批注已自动更新。';
    } catch(e) {
      // A transient read failure must not interrupt editing or disable saving.
    } finally {polling=false;}
  }
  if(!readonly)setInterval(pollUpdates,2000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollUpdates();});
  const phases={queued:'排队',read:'读取批注',lock:'等待存储锁',validate:'检查版本',locate:'定位批注',preflight:'预检',apply:'应用',reuse:'复用预检',write:'写入',rebuild:'更新报告',complete:'完成'};
  function showProgress(job) {status.textContent=`${phases[job.phase]||job.phase} · ${job.total?`${job.completed}/${job.total}`:'处理中'}`;}
  let jobsPolling=false,externalJob=false;
  async function pollJobs() {
    if(readonly||busy||jobsPolling||document.hidden||!ready)return;
    jobsPolling=true;
    try {const data=await adapter.readJobs();if(busy)return;if(data.jobs.length){externalJob=true;showProgress(data.jobs.at(-1));}else if(externalJob){externalJob=false;status.textContent='批注任务已结束。';pollUpdates();}}
    catch(e) {} finally {jobsPolling=false;}
  }
  if(!readonly)setInterval(pollJobs,1000);
  function acceptSnapshot(s) {
    if(!s.incremental){snapshot=s;return null;}
    if(s.start_revision!==snapshot.revision)throw Error('批注修订号已变化，请重新读取');
    const changed=new Set([...s.threads.map(t=>t.id),...(s.deleted_thread_ids||[])]);
    const updates=new Map(s.threads.map(t=>[t.id,t])),deleted=new Set(s.deleted_thread_ids||[]);
    const threads=snapshot.threads.filter(t=>!deleted.has(t.id)).map(t=>{const next=updates.get(t.id);updates.delete(t.id);return next||t;});
    threads.push(...updates.values());snapshot={...snapshot,...s,threads};return changed;
  }
  async function synchronize() {
    if(busy||!ready)throw Error('批注接口或版本不可用');
    busy=true;++readGeneration;
    try{
      const s=await adapter.readChanges(snapshot.revision);
      check(content,s);render(acceptSnapshot(s));
    }finally{busy=false;}
  }
  async function update(request) {
    if(readonly){status.textContent='历史快照只读。';return false;}
    if(!ready&&preparation&&!busy)await preparation;
    if(!ready||busy) {status.textContent='批注接口或版本不可用。输入已保留。'; return false;}
    remember(); busy=true; save.disabled=true;
    status.textContent='正在提交批注。';
    try {
      check(content,snapshot); ++readGeneration;
      const envelope={project_id:content.project_id,version_id:content.version_id,request:{...request,request_id:request.request_id||crypto.randomUUID(),expected_revision:snapshot.revision}};
      if(['suggestion_preview','suggestion_apply','import','reconcile'].includes(request.operation))delete envelope.request.request_id;
      const s=await adapter.update(envelope,showProgress);
      check(content,s);
      if(s.dry_run){status.textContent='批注预检已完成。';return s.results.every(r=>r.ok);}
      if(!Array.isArray(s.threads)){await refresh();status.textContent='已核对原提交收据。';return Boolean(s.committed);}
      const changed=acceptSnapshot(s);
      if(request.operation==='create'){scope=null;if(editing)active=s.result?.thread_id||s.threads.at(-1)?.id;}
      render(changed); if(s.result?.source_changed&&s.result?.rebuild?.status==='updated')setTimeout(()=>window.location.reload(),800); status.textContent=s.result?.source_changed?'源码已修改。'+(s.result?.rebuild?.message||'报告更新状态请检查服务反馈。'):'批注已保存。';
      if(request.operation==='batch'){const failed=s.results.filter(r=>!r.ok);status.textContent=failed.length?`批量提交：${s.results.length-failed.length} 条成功，${failed.length} 条失败。`:'批注已保存。';return s.committed;}
      return true;
    } catch(e) { await refresh(); recheck.hidden=false;status.textContent=e.message+' 输入已保留。请检查后重新保存。'; return false; }
    finally {busy=false;save.disabled=false;}
  }
  let filteredCache=null,filteredKey=null;
  function filtered(changed=null) {
    const q=search.value.toLowerCase(),key=JSON.stringify([scope,identity.value,category.value,resolved.value,location.value,q,sort.value]);
    const matches=t=>{
      const m=t.messages.filter(x=>!x.deleted_at),a=t.association;
      return (!scope||scope.includes(t.id))&&(!identity.value||m.some(x=>x.author.type===identity.value))&&(!category.value||m.some(x=>x.category===category.value))&&(!resolved.value||String(t.resolved)===resolved.value)&&(!location.value||a.status===location.value)&&(!q||[t.original_anchor.quote,a.anchor?.quote,...m.flatMap(x=>[x.body,x.author.name])].join(' ').toLowerCase().includes(q));
    };
    const compare=(a,b)=>sort.value==='position'?((a.association.anchor?.order??1e12)-(b.association.anchor?.order??1e12)||(a.association.anchor?.start??0)-(b.association.anchor?.start??0)||a.created_at.localeCompare(b.created_at)):a[sort.value].localeCompare(b[sort.value]);
    if(changed&&filteredCache&&filteredKey===key){
      filteredCache=filteredCache.filter(t=>!changed.has(t.id));
      for(const t of snapshot.threads){
        if(!changed.has(t.id)||!matches(t))continue;
        let lo=0,hi=filteredCache.length;
        while(lo<hi){const mid=(lo+hi)>>1;if(compare(filteredCache[mid],t)<=0)lo=mid+1;else hi=mid;}
        filteredCache.splice(lo,0,t);
      }
    }else filteredCache=snapshot.threads.filter(matches).sort(compare);
    filteredKey=key;return filteredCache;
  }
  function step(delta) {scope=null;const ts=filtered();if(ts.length){const i=ts.findIndex(t=>t.id===active);focus(ts[i<0?(delta>0?0:ts.length-1):(i+delta+ts.length)%ts.length]);}}
  function paintActive() {
    const ranges=[];
    document.querySelectorAll('.annotation-active-formula').forEach(e=>e.classList.remove('annotation-active-formula'));
    const t=panel.open?snapshot.threads.find(t=>t.id===active):null,a=t?.association.status==='needs_relocation'?null:t?.association.anchor;
    if(a) {
      const root=rootFor(a);
      if(root&&a.kind==='formula')root.classList.add('annotation-active-formula');
      if(root&&a.kind==='text') {const map=coordinates(root);if(map.text===a.unit_text&&a.start>=0&&a.end>a.start&&a.end<=map.entries.length)ranges.push(map.range(a.start,a.end));}
    }
    if(window.CSS?.highlights&&window.Highlight) {const highlight=new Highlight(...ranges);highlight.priority=1;CSS.highlights.set('annotation-active',highlight);}
    document.querySelectorAll('.annotation-card').forEach(c=>{const selected=panel.open&&c.dataset.annotationThread===active;c.classList.toggle('is-active',selected);c.querySelector(':scope > summary')?.setAttribute('aria-current',String(selected));c.open=selected;});
  }
  function focus(t) {
    remember();active=t.id;panel.open=true;render(new Set([t.id]));
    const a=t.association.status==='needs_relocation'?t.association.container:t.association.anchor;
    if(a){const root=rootFor(a);(a.scope==='table'?root?.closest('table')||root:root)?.scrollIntoView({block:'center'});}
    else status.textContent='待重新定位。请选择当前正文进行手动关联。';
    document.querySelector(`[data-annotation-thread="${CSS.escape(t.id)}"]`)?.scrollIntoView({block:'nearest'});
    paintActive();
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
  function authorLine(message,includeCategory=false) {
    const line=el('div',null,'annotation-author-line');
    line.append(el('span',message.author.name,'annotation-author-name'),el('span',message.author.type==='agent'?'智能体':'用户','annotation-tag annotation-identity-tag'));
    if(includeCategory)line.append(el('span',categories.find(x=>x[0]===message.category)?.[1]||message.category,'annotation-tag annotation-category-tag'));
    const date=new Date(message.created_at),time=el('time',date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}),'annotation-message-time');
    time.dateTime=message.created_at;time.title=date.toLocaleString('zh-CN');line.append(time);
    return line;
  }
  function messageRow(message,reply=false) {
    const row=el('div',null,reply?'annotation-message annotation-reply':'annotation-message');
    if(reply)row.append(authorLine(message,true));
    row.dataset.annotationMessage=message.id;
    safeMath(row,message.body);
    return row;
  }
  function card(t) {
    const d=el('details',null,'annotation-card');d.open=t.id===active;d.dataset.annotationThread=t.id;
    d.addEventListener('click',e=>{if(!e.target.closest('button,input,textarea,select')&&(!e.target.closest('details')||e.target.closest('details')===d)){if(e.target.closest('summary'))e.preventDefault();if(active!==t.id||!d.open)focus(t);}});
    const messages=t.messages.filter(m=>!m.deleted_at),rootMessage=messages[0],replies=messages.slice(1);
    const title=el('summary'),tags=el('div',null,'annotation-card-tags');
    title.append(rootMessage?authorLine(rootMessage):el('span','空批注','annotation-author-name'));
    tags.append(el('span',t.resolved?'已解决':'未解决','annotation-tag annotation-status-tag'+(t.resolved?' is-resolved':'')));
    if(rootMessage)tags.append(el('span',categories.find(x=>x[0]===rootMessage.category)?.[1]||rootMessage.category,'annotation-tag annotation-category-tag'));
    title.append(tags,el('span',rootMessage?.body||'空批注','annotation-card-preview'));
    if(replies.length)title.append(el('span',`${replies.length} 条回复`,'annotation-card-reply-count'));
    d.append(title);
    if(t.association.status==='needs_relocation') {
      d.append(el('p',t.association.container?.selection_deleted?'原选区已删除。批注仍关联到所属段落或表格。':t.association.container?'已定位所属段落或表格；选区待重新定位。':'待重新定位'));
      if(t.association.container)d.append(button('定位所属段落或表格',()=>focus(t)));
      if(!readonly)d.append(button('手动重新关联',()=>{relocation=t;status.textContent='请选择当前段落内文字或完整公式。';}));
    }
    if(t.association.status==='auto_migrated') d.append(el('p','自动迁移'));
    const replySection=el('section',null,'annotation-replies');replySection.setAttribute('aria-label',`回复 · ${replies.length}`);
    if(replies.length)replySection.append(el('h3',`回复 · ${replies.length}`,'annotation-replies-heading'));
    const replyRows=new Map();
    messages.forEach((m,index)=>{
      const row=messageRow(m,index>0);
      if(m.suggested_replacement){
        const suggestion=m.suggested_replacement,box=el('details',null,'annotation-suggestion');box.append(el('summary',suggestion.status==='applied'?'源码建议 · 已应用':'源码替换建议'));
        box.append(el('pre',suggestion.original_chunk),el('pre',suggestion.replacement_chunk));
        const diff=el('pre',suggestion.reason||'点击预览检查源码差异。');box.append(diff);
        const preview=button('预览源码差异',async()=>{try{const envelope={project_id:content.project_id,version_id:content.version_id,request:{operation:'suggestion_preview',thread_id:t.id,message_id:m.id,expected_revision:snapshot.revision}};const response=await adapter.update(envelope);diff.textContent=response.result.diff;}catch(e){diff.textContent=e.message;}});
        if(!readonly)box.append(preview); if(!readonly&&suggestion.status!=='applied')box.append(button('接受并应用源码建议',()=>update({operation:'suggestion_apply',thread_id:t.id,message_id:m.id})));row.append(box);
      }
      const edit=button('编辑',()=>openComposer('edit',t,null,m));
      edit.className='annotation-edit';if(!readonly)row.append(edit);
      if(!readonly&&index>0){const reply=button('回复',()=>openComposer('reply',t,null,m));reply.className='annotation-edit';row.append(document.createTextNode(' · '),reply);}
      if(index===0)d.append(row);else {
        const parent=m.reply_to?messages.slice(1,index).find(x=>x.id===m.reply_to):null;
        const parentRow=parent&&replyRows.get(parent.id);
        if(parentRow){
          let children=parentRow.querySelector(':scope > .annotation-reply-children');
          if(!children){children=el('div',null,'annotation-reply-children');parentRow.append(children);}
          row.classList.add('annotation-nested-reply');children.append(row);
        }else replySection.append(row);
        replyRows.set(m.id,row);
      }
    });
    if(replies.length)d.append(replySection);
    const actions=el('div',null,'annotation-card-actions');
    actions.append(button('回复',()=>openComposer('reply',t)),button(t.resolved?'重新打开':'标记为已解决',()=>update({operation:t.resolved?'reopen':'resolve',thread_id:t.id})));
    const remove=button('删除批注',()=>{const replies=Math.max(0,t.messages.filter(m=>!m.deleted_at).length-1);if(window.confirm(`删除这条批注及其 ${replies} 条回复？删除后无法在界面恢复。`))update({operation:'delete_thread',thread_id:t.id});});remove.className='annotation-delete';actions.append(remove);if(!readonly)d.append(actions);
    if(t.history.length) {const history=el('details',null,'annotation-message-actions');history.append(el('summary','关联记录'));t.history.forEach(h=>history.append(el('p',h.at+' · '+h.method)));d.append(history);}
    return d;
  }
  const renderedCards=new Map(),renderedThreads=new Map(),threadPlacements=new Map(),placementGroups=new Map(),unitAttachments=new Map(),coordinateMaps=new Map(),rangeCache=new Map();
  let placementVersion=null;
  function removePlacement(id) {
    const prior=threadPlacements.get(id);if(!prior)return;
    const group=placementGroups.get(prior.attachment);group?.threads.delete(id);if(group)group.dirty=true;
    threadPlacements.delete(id);
  }
  function placeThread(t) {
    removePlacement(t.id);
    const a=t.association.status==='needs_relocation'?t.association.container:t.association.anchor;
    if(!a)return;
    const root=rootFor(a);if(!root)return;
    let attachment=unitAttachments.get(a.unit_id);
    if(!attachment){
      const prose=content.units.find(u=>u.kind==='text'&&rootFor(u)?.contains(root));
      attachment=a.scope==='table'?root.closest('table')||root:prose?rootFor(prose):(a.kind==='formula'?root.parentElement:root);
      unitAttachments.set(a.unit_id,attachment);
    }
    let range=null;
    if(a.kind==='text'){
      let map=coordinateMaps.get(root);if(!map){map=coordinates(root);coordinateMaps.set(root,map);}
      if(map.text===a.unit_text&&a.start>=0&&a.end>a.start&&a.end<=map.entries.length){const key=`${a.unit_id}:${a.start}:${a.end}`;range=rangeCache.get(key);if(!range){range=map.range(a.start,a.end);rangeCache.set(key,range);}}
    }
    let group=placementGroups.get(attachment);
    if(!group){group={threads:new Map(),dirty:true,mark:null};placementGroups.set(attachment,group);}
    group.threads.set(t.id,t);group.dirty=true;
    threadPlacements.set(t.id,{attachment,root,range,kind:a.kind});
  }
  function render(changed=null) {
    if(placementVersion!==content){
      for(const group of placementGroups.values())group.mark?.remove();
      placementGroups.clear();threadPlacements.clear();unitAttachments.clear();coordinateMaps.clear();rangeCache.clear();placementVersion=content;
    }
    const byId=new Map(snapshot.threads.map(t=>[t.id,t]));
    if(changed){for(const id of changed){const t=byId.get(id);if(t)placeThread(t);else removePlacement(id);}}
    else{
      for(const id of threadPlacements.keys())if(!byId.has(id))removePlacement(id);
      for(const t of snapshot.threads)if(!threadPlacements.has(t.id)||renderedThreads.get(t.id)!==t)placeThread(t);
    }
    const visible=filtered(changed);
    if(!visible.some(t=>t.id===active))active=null;
    position.textContent=visible.length?`${active?visible.findIndex(t=>t.id===active)+1:0} / ${visible.length}`:'0 / 0';
    navigation.querySelectorAll('button').forEach(b=>b.disabled=!visible.length);
    heading.querySelector('h2').textContent=scope?'此处批注':'批注';all.textContent=scope?'全部批注':'全部批注 · '+visible.length;
    // Bounded cards keep large projects responsive; navigation/search expose all threads.
    const offset=Math.max(0,visible.findIndex(t=>t.id===active));
    const shown=visible.slice(Math.floor(offset/100)*100,Math.floor(offset/100)*100+100);
    const wanted=new Set(shown.map(t=>t.id));
    for(const [id,c] of renderedCards)if(!wanted.has(id)){c.remove();renderedCards.delete(id);renderedThreads.delete(id);}
    const fragment=document.createDocumentFragment();list.replaceChildren();
    unlocated.querySelectorAll(':scope > .annotation-empty').forEach(e=>e.remove());
    for(const t of shown){
      const li=el('li'),summary=t.messages.find(m=>!m.deleted_at)?.body||'批注';
      li.append(button(`${t.resolved?'已解决':'未解决'} · ${summary.length>80?summary.slice(0,80)+'…':summary}`,()=>focus(t)));fragment.append(li);
      let c=renderedCards.get(t.id);
      if(!c||renderedThreads.get(t.id)!==t||changed?.has(t.id)){const next=card(t);if(c)c.replaceWith(next);c=next;renderedCards.set(t.id,c);renderedThreads.set(t.id,t);}
      const index=shown.indexOf(t),current=unlocated.children[index];if(current!==c)unlocated.insertBefore(c,current||null);
    }
    list.append(fragment);
    if(visible.length>100){const more=el('li');more.append(button(`显示 ${Math.floor(offset/100)*100+1}–${Math.floor(offset/100)*100+shown.length} / ${visible.length} · 下一页`,()=>{active=visible[(Math.floor(offset/100)*100+100)%visible.length].id;render();}));list.append(more);}
    if(!visible.length)unlocated.append(el('p',scope?'此处没有符合条件的批注。':'没有符合条件的批注。','annotation-empty'));
    const highlights=[],highlightKeys=new Set();
    document.querySelectorAll('.annotation-located-formula').forEach(e=>e.classList.remove('annotation-located-formula'));
    for(const [attachment,group] of placementGroups){
      const threads=[...group.threads.values()].filter(t=>!t.resolved||resolved.value!=='false');
      if(!threads.length){group.mark?.remove();group.mark=null;continue;}
      if(!group.mark){group.mark=button('',()=>{const t=[...group.threads.values()].find(t=>!t.resolved||resolved.value!=='false');if(t){scope=null;focus(t);}});group.mark.className='annotation-mark';attachment.append(group.mark);}
      const association=threads[0].association;
      group.mark.dataset.unit=(association.status==='needs_relocation'?association.container:association.anchor).unit_id;
      if(group.dirty||group.mark.textContent!==String(threads.length)){group.mark.textContent=String(threads.length);group.mark.setAttribute('aria-label',`查看此处 ${threads.length} 条批注`);group.mark.title=`${threads.length} 条批注`;}group.dirty=false;
      for(const t of threads){const placed=threadPlacements.get(t.id);if(placed?.range){const a=t.association.anchor,key=`${a.unit_id}:${a.start}:${a.end}`;if(!highlightKeys.has(key)){highlightKeys.add(key);highlights.push(placed.range);}}if(placed?.kind==='formula')placed.root.classList.add('annotation-located-formula');}
    }
    if(window.CSS?.highlights&&window.Highlight)CSS.highlights.set('annotations',new Highlight(...highlights));
    for(const formula of pendingMath.keys())if(!formula.isConnected)pendingMath.delete(formula);
    paintActive();document.body.classList.remove('annotation-margin-active');document.dispatchEvent(new CustomEvent('annotation-layout'));
  }
  function arrange() {}
  window.reviewAnnotationMargin={entries:()=>layoutCards,arrange};
  window.addEventListener('resize',arrange);window.addEventListener('review-math-ready',arrange);document.querySelector('main').addEventListener('load',arrange,true);if(document.fonts)document.fonts.ready.then(arrange);document.addEventListener('annotation-layout',()=>{if(!window.reviewNavigation)requestAnimationFrame(arrange);});
  margin.addEventListener('toggle',()=>{arrange();document.dispatchEvent(new CustomEvent('annotation-layout'));},true);
  function overlap(a) {
    const matches=snapshot.threads.filter(t=>{const b=t.association.anchor;return b?.unit_id===a.unit_id&&(a.kind==='formula'||b.start<a.end&&b.end>a.start);}).map(t=>t.id);
    scope=null;const t=snapshot.threads.find(t=>t.id===matches[0]);if(t)focus(t);
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
    if(readonly)return;
    if(panel.contains(e.target)||editor.contains(e.target)||toolbar.contains(e.target)) return;
    toolbar.hidden=true;if(!ready) return;
    try { pending=selection(); if(pending&&relocation) {const t=relocation;relocation=null;update({operation:'associate',thread_id:t.id,selection:pending});return;}
      if(pending&&editing?.operation==='create'&&!editing.selection){editing.selection=pending;editing.content=structuredClone(content);editing.anchor=window.reviewAnnotationDelivery?.anchor(content,pending);editing.warning=null;remember();status.textContent='草稿已关联到当前选区。';return;}
      if(pending) {toolbar.hidden=false;const rect=getSelection().getRangeAt(0).getBoundingClientRect();toolbar.style.top=Math.max(4,rect.bottom+6)+'px';toolbar.style.left=Math.min(innerWidth-130,Math.max(4,rect.left))+'px';} } catch(e) {status.textContent=e.message;}
  });
  document.addEventListener('click',e=>{
    if(!ready||e.target.closest('.annotation-overview,.annotation-mark')) return;
    const point=document.caretRangeFromPoint?.(e.clientX,e.clientY);
    if(point&&getSelection().isCollapsed&&!(document.body.classList.contains('live-view')&&document.body.classList.contains('bilingual-view')&&e.target.closest('.review-sentence,.translated-sentence'))) {
      for(const u of content.units.filter(u=>u.kind==='text')) {const root=rootFor(u);if(root?.contains(point.startContainer)){const map=coordinates(root),offset=map.offset(point.startContainer,point.startOffset);const t=snapshot.threads.find(t=>{const a=t.association.anchor;return a?.unit_id===u.id&&a.start<=offset&&a.end>offset;});if(t) overlap({...t.association.anchor,start:offset,end:offset+1});break;}}
    }
    const formula=e.target.closest('[data-annotation-latex]'); if(formula&&!readonly) {const u=content.units.find(u=>u.kind==='formula'&&rootFor(u)===formula);if(u){pending={unit_id:u.id,side:'new'};if(relocation){const t=relocation;relocation=null;update({operation:'associate',thread_id:t.id,selection:pending});return;}if(editing?.operation==='create'&&!editing.selection){editing.selection=pending;editing.content=structuredClone(content);editing.anchor=window.reviewAnnotationDelivery?.anchor(content,pending);editing.warning=null;remember();return;}toolbar.hidden=false;const r=formula.getBoundingClientRect();toolbar.style.top=r.bottom+'px';toolbar.style.left=Math.min(innerWidth-130,r.left)+'px';}}
  });
  [sort,identity,category,resolved,location,search].forEach(e=>e.addEventListener('input',()=>{scope=null;render();}));
  window.reviewAnnotations=Object.freeze({coordinates,selection,refresh,synchronize,update,openComposer,focus,
    getDrafts(){remember(false);return structuredClone(drafts);},restoreDrafts(value){if(readonly)return;Object.assign(drafts,value);const tray=el('div',null,'annotation-drafts');tray.append(el('p','保留的编辑草稿'));Object.entries(value).forEach(([key,d])=>tray.append(button(d.body.slice(0,60)||'空草稿',()=>window.reviewAnnotations.resumeDraft(key))));panel.append(tray);},resumeDraft(key){const d=drafts[key];if(d){openComposer(d.operation,d.thread,d.selection,d.message);editing={...d,key};body.value=d.body;name.value=d.name;author.value=d.author;type.value=d.category;original.value=d.original||'';replacement.value=d.replacement||'';if(d.warning)status.textContent=d.warning;}},
    setAdapter(value){adapter=value;return refresh();},isReady(){return ready;},isBusy(){return busy||externalJob;},getContent(){return content;},getSnapshot(){return snapshot;}});
})();
