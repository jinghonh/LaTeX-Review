/* Portable annotation contract. IndexedDB transactions serialize same-origin windows. */
(async () => {
  'use strict';
  const readonly = document.querySelector('meta[name="latex-review-readonly"]')?.content === 'snapshot';
  const api=window.reviewAnnotations;
  const readJSON=id=>JSON.parse(document.getElementById(id)?.textContent||'null');
  if(!api)return;
  const codec=__ANNOTATION_CODEC__;
  const packed=document.getElementById('annotation-backup-gzip');
  let embedded;
  if(packed){
    document.getElementById('annotation-status').textContent='批注正在等待加载。';
    // Let the document paint before decoding history. Interaction requests it sooner.
    await new Promise(resolve=>{
      let done=false,id;
      const events=['toggle','pointerup','keydown'];
      const load=e=>{if(e?.type==='toggle'&&(e.target.id!=='annotation-overview'||!e.target.open))return;if(done)return;done=true;events.forEach(e=>document.removeEventListener(e,load,true));if(id)cancelIdleCallback(id);resolve();};
      events.forEach(e=>document.addEventListener(e,load,true));
      requestAnimationFrame(()=>requestAnimationFrame(()=>{if(done)return;if(window.requestIdleCallback)id=requestIdleCallback(load,{timeout:1500});else setTimeout(load,0);}));
    });
    performance.mark('annotation-decode-start');
    try{
      embedded=await codec.decodeBackup(JSON.parse(packed.textContent));
      performance.mark('annotation-decode-end');performance.measure('annotation-decode','annotation-decode-start','annotation-decode-end');
    }catch(e){document.getElementById('annotation-status').textContent='批注备份读取失败：'+e.message+'。浏览器记录未覆盖。';window.reviewAnnotationDeliveryError=e.message;return;}
  }else embedded=readJSON('annotation-backup');
  const currentVersion=readJSON('annotation-current-version');
  let content=readJSON('annotation-content')||(currentVersion&&embedded?.versions?.[currentVersion]);
  if(!api)return;
  if(!content){
    if(readonly){document.getElementById('annotation-status').textContent='历史快照未包含批注。';return;}
    if(location.protocol==='file:'){document.getElementById('annotation-status').textContent='请使用 latex-review-preview 打开本机报告，或使用独立导出的 HTML。';return;}
    try{const response=await fetch('api/annotations/content');if(!response.ok)throw Error('正文读取失败');content=await response.json();}
    catch(e){document.getElementById('annotation-status').textContent=e.message;return;}
  }
  const copy=value=>structuredClone(value),stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
  const fail=message=>{throw Error(message);};
  const text=(value,label,limit=65536)=>{if(typeof value!=='string'||!value.trim()||new TextEncoder().encode(value).length>limit) fail(label+'无效');return value;};
  const uuid=()=>crypto.randomUUID?crypto.randomUUID():([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));
  const selection=a=>a.kind==='formula'?{unit_id:a.unit_id,side:'new'}:{unit_id:a.unit_id,start:a.start,end:a.end,quote:a.quote,side:'new'};
  function anchor(c,s) {
    keys(s,['unit_id','start','end','end_unit_id','side','quote'],['unit_id']);
    if('side' in s&&s.side!=='new'||s.end_unit_id&&s.end_unit_id!==s.unit_id) fail('只能选择当前段落或完整公式');
    const u=c.units.find(u=>u.id===s.unit_id);if(!u) fail('正文单元不存在');
    let start=null,end=null,quote=u.raw_latex,prefix='',suffix='';
    if(u.kind==='text') {
      start=s.start;end=s.end;
      if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>u.text.length||/[\uD800-\uDBFF]/.test(u.text[start-1]||'')||/[\uDC00-\uDFFF]/.test(u.text[end]||'')) fail('选区边界无效');
      quote=u.text.slice(start,end);prefix=Array.from(u.text.slice(0,start)).slice(-64).join('');suffix=Array.from(u.text.slice(end)).slice(0,64).join('');
    } else if('start' in s||'end' in s) fail('公式必须完整选择');
    if(s.quote!=null&&s.quote!==quote) fail('选区原文与版本不一致');
    return {...(u.source_span?{source_span:copy(u.source_span)}:{}),...(u.subunit?{subunit:copy(u.subunit)}:{}),version_id:c.version_id,unit_id:u.id,kind:u.kind,start,end,quote,prefix,suffix,unit_text:u.text,raw_latex:u.raw_latex,node_id:u.node_id,source:copy(u.source),section_path:copy(u.section_path),labels:copy(u.labels),order:u.order,selector:u.selector,change_ids:copy(u.change_ids),atoms:copy(u.atoms.filter(a=>start!=null&&start<=a.offset&&a.offset<end))};
  }
  function semantic(value) {
    const v=copy(value); delete v.selector;delete v.change_ids;delete v.order;
    if(v.atoms) v.atoms.forEach(a=>delete a.selector);return stable(v);
  }
  function keys(value,fields,required=fields) {if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!fields.includes(k))||required.some(k=>!(k in value)))fail('备份字段不符合契约');}
  function unitSemantic(u){const value=copy(u);delete value.selector;delete value.change_ids;value.atoms?.forEach(a=>delete a.selector);return stable(value);}
  function validateContent(c) {
    keys(c,['schema_version','project_id','project_root','entry','version_id','coordinate_system','units','content_model'],['schema_version','project_id','entry','version_id','coordinate_system','units']);
    if(c?.schema_version!==content.schema_version||c.project_id!==content.project_id||!/^[a-f0-9]{64}$/.test(c.version_id)||c.coordinate_system!==content.coordinate_system||!Array.isArray(c.units)) fail('项目或正文版本无效');
    const ids=new Set(),orders=new Set();for(const u of c.units) {
      keys(u,['id','kind','node_id','parent_node_id','text','raw_latex','atoms','selector','source','section_path','labels','order','change_ids','source_span','subunit'],['id','kind','node_id','text','raw_latex','atoms','selector','source','section_path','labels','order','change_ids']);
      if(!Number.isInteger(u.order)||u.order<0||orders.has(u.order)||!u.source||typeof u.source!=='object'||['section_path','labels','change_ids'].some(k=>!Array.isArray(u[k])||u[k].some(x=>typeof x!=='string')))fail('正文上下文无效');orders.add(u.order);
      const offsets=new Set();for(const a of u.atoms){keys(a,['offset','formula_node_id','raw_latex','selector']);if(!Number.isInteger(a.offset)||offsets.has(a.offset)||u.text[a.offset]!=='\ufffc'||typeof a.raw_latex!=='string'||typeof a.selector!=='string')fail('公式原子无效');offsets.add(a.offset);}
      if(u.kind==='formula'&&(u.atoms.length||u.text!==u.raw_latex))fail('完整公式无效');if(ids.has(u.id)||!['text','formula'].includes(u.kind)||typeof u.text!=='string'||typeof u.raw_latex!=='string'||!Array.isArray(u.atoms)) fail('正文单元无效');ids.add(u.id);}
  }
  const suggestionSchema=__SUGGESTION_SCHEMA__;
  function validateSuggestion(value) {
    const fields=suggestionSchema.properties,spanFields=fields.source_span.properties;
    keys(value,Object.keys(fields),suggestionSchema.required);
    for(const name of ['original_chunk','replacement_chunk']) {
      const text=value[name],rule=fields[name];
      if(typeof text!=='string'||Array.from(text).length<(rule.minLength||0)||Array.from(text).length>rule.maxLength)fail('建议 LaTeX 块无效');
    }
    if('status' in value&&!fields.status.enum.includes(value.status))fail('建议应用状态无效');
    if(value.reason!=null&&typeof value.reason!=='string')fail('建议降级理由无效');
    const span=value.source_span;
    if(span!=null) {
      keys(span,Object.keys(spanFields),fields.source_span.required);
      if(typeof span.file!=='string'||!new RegExp(spanFields.file.pattern).test(span.file)||
         typeof span.sha256!=='string'||span.sha256.length>spanFields.sha256.maxLength||!new RegExp(spanFields.sha256.pattern).test(span.sha256)||
         span.reliable!==spanFields.reliable.const||span.writable!==spanFields.writable.const||
         !Number.isInteger(span.start)||span.start<spanFields.start.minimum||!Number.isInteger(span.end)||span.end<=span.start||
         span.original_chunk!==value.original_chunk||Array.from(value.original_chunk).length!==span.end-span.start)fail('建议源码区间无效');
    }
  }
  function validateBundle(b) {
    keys(b,['schema_version','project_id','revision','versions','threads']);
    if(b?.schema_version!==content.schema_version||b.project_id!==content.project_id||!Number.isInteger(b.revision)||b.revision<0||!b.versions||!b.threads||Array.isArray(b.versions)||Array.isArray(b.threads)) fail('备份项目或契约无效');
    for(const [id,c] of Object.entries(b.versions)) {validateContent(c);if(id!==c.version_id) fail('版本身份不一致');}
    const check=a=>{const c=b.versions[a?.version_id];if(!c||semantic(anchor(c,selection(a)))!==semantic(a)) fail('备份锚点与正文不一致');};
    for(const [id,t] of Object.entries(b.threads)) {
      keys(t,['id','project_id','created_version_id','created_at','updated_at','resolved','deleted_at','messages','original_anchor','current_anchor','associations','history','title','tags'],['id','project_id','created_version_id','created_at','updated_at','resolved','deleted_at','messages','original_anchor','current_anchor','associations','history']);metadata(t);
      if(id!==t.id||t.project_id!==b.project_id||typeof t.resolved!=='boolean'||!Array.isArray(t.messages)||!t.messages.length||!Array.isArray(t.history)||!t.associations) fail('线程无效');
      check(t.original_anchor);check(t.current_anchor);if(t.created_version_id!==t.original_anchor.version_id) fail('创建版本无效');
      const ids=new Set();for(const m of t.messages) {keys(m,['id','body','author','category','created_at','updated_at','deleted_at','edits','reply_to','suggested_replacement'],['id','body','author','category','created_at','updated_at','deleted_at','edits']);if('reply_to' in m&&!ids.has(m.reply_to))fail('回复对象必须是先前消息');text(m.id,'内容 ID');if(ids.has(m.id)) fail('内容 ID 重复');ids.add(m.id);messageFields(m);if('suggested_replacement' in m)validateSuggestion(m.suggested_replacement);if(!Array.isArray(m.edits)) fail('编辑历史无效');for(const e of m.edits){keys(e,['body','category','at']);text(e.body,'历史正文');category(e.category);text(e.at,'时间');}for(const key of ['created_at','updated_at'])text(m[key],'时间');if(m.deleted_at!=null)text(m.deleted_at,'时间');}
      for(const [version,a] of Object.entries(t.associations)) {keys(a,['status','anchor','candidates','reason','container'],['status','anchor','candidates','reason']);if(!b.versions[version]||!['exact','auto_migrated','manual','needs_relocation'].includes(a.status)||!Array.isArray(a.candidates)) fail('关联无效');if(a.anchor){check(a.anchor);if(a.anchor.version_id!==version)fail('关联版本无效');}if(a.container){const u=b.versions[version].units.find(u=>u.id===a.container.unit_id);keys(a.container,['unit_id','kind','selector','order','source','section_path','selection_deleted','subunit','scope'],['unit_id','kind','selector','order','source','section_path','selection_deleted']);if(!u||typeof a.container.selection_deleted!=='boolean'||a.container.kind!==u.kind||('scope' in a.container&&(a.container.scope!=='table'||!['cell','header'].includes(u.subunit?.type)))||stable(a.container.source)!==stable(u.source)||stable(a.container.section_path)!==stable(u.section_path))fail('容器关联无效');}for(const candidate of a.candidates){keys(candidate,['anchor','score','method','context_score'],['anchor','score','method']);check(candidate.anchor);if(candidate.anchor.version_id!==version||typeof candidate.score!=='number'||candidate.score<0||candidate.score>1||!['exact','fuzzy'].includes(candidate.method))fail('候选无效');}}
      for(const h of t.history){keys(h,['at','method','anchor']);check(h.anchor);text(h.at,'时间');if(!['created','exact','auto_migrated','manual'].includes(h.method))fail('历史类型无效');}
      for(const key of ['created_at','updated_at'])text(t[key],'时间');if(t.deleted_at!=null)text(t.deleted_at,'时间');
    }
  }
  function metadata(v){
    const result={};
    if('title' in v){if(typeof v.title!=='string'||new TextEncoder().encode(v.title).length>512)fail('title 须为不超过 512 字节的文字');result.title=v.title.trim();}
    if('tags' in v){if(!Array.isArray(v.tags)||v.tags.length>32)fail('tags 须为最多 32 个标签的列表');result.tags=v.tags.map(t=>{text(t,'tag',128);return t.trim();});if(new Set(result.tags).size!==result.tags.length)fail('tags 不得包含重复标签');}
    return result;
  }
  function category(v){if(!['question','suggestion','note'].includes(v))fail('分类无效');return v;}
  function messageFields(r){keys(r.author,['name','type']);text(r.body,'批注正文');category(r.category);text(r.author?.name,'作者',256);if(!['user','agent'].includes(r.author.type))fail('作者身份无效');}
  function importBundle(data,b) {
    validateBundle(b);
    const conflicts=Object.keys(b.threads).filter(id=>data.threads[id]&&stable(data.threads[id])!==stable(b.threads[id]));
    for(const [id,c] of Object.entries(b.versions))if(data.versions[id]&&stable(data.versions[id].units.map(unitSemantic))!==stable(c.units.map(unitSemantic)))conflicts.push(id);
    if(conflicts.length)fail('导入存在同 ID 内容冲突：'+conflicts.join(', '));
    const imported=Object.keys(b.threads).filter(id=>!data.threads[id]);Object.assign(data.threads,copy(b.threads));Object.assign(data.versions,copy(b.versions));return {imported,duplicates:Object.keys(b.threads).length-imported.length};
  }
  // Offline matching is deliberately conservative. Ambiguity always requires selection.
  function locate(c,a) {
    const candidates=[];
    for(const u of c.units.filter(u=>u.kind===a.kind)) {
      if(u.kind==='formula') {if(u.raw_latex===a.raw_latex)candidates.push({anchor:anchor(c,{unit_id:u.id}),score:1,method:'exact'});}
      else {let at=u.text.indexOf(a.quote);while(at>=0){const next=anchor(c,{unit_id:u.id,start:at,end:at+a.quote.length});if(stable(next.atoms.map(x=>x.raw_latex))===stable(a.atoms.map(x=>x.raw_latex)))candidates.push({anchor:next,score:u.text===a.unit_text?1:.85,method:'exact',context_score:u.text===a.unit_text?1:0});at=u.text.indexOf(a.quote,at+1);}}
    }
    return candidates.length===1&&candidates[0].score===1?{status:'exact',anchor:candidates[0].anchor,candidates:[],reason:null}:{status:'needs_relocation',anchor:null,candidates:candidates.slice(0,8),reason:'离线匹配需要唯一完整原文；请选择当前正文重新关联。'};
  }
  function view(data) {
    return {schema_version:data.schema_version,project_id:data.project_id,version_id:content.version_id,revision:data.revision,threads:Object.values(data.threads).filter(t=>!t.deleted_at).map(t=>{
      const result=copy(t),a=copy(t.associations[content.version_id]||locate(content,t.current_anchor));
      if(!t.associations[content.version_id])a.persisted=false;
      if(a.container){const u=content.units.find(u=>u.id===a.container.unit_id);if(u)Object.assign(a.container,{selector:u.selector,order:u.order});}if(a.anchor)a.anchor=anchor(content,selection(a.anchor));a.candidates.forEach(x=>x.anchor=anchor(content,selection(x.anchor)));result.association=a;return result;
    })};
  }
  if(readonly) {
    if(!embedded){document.getElementById('annotation-status').textContent='历史快照未包含批注。';return;}
    validateBundle(embedded);
    const frozen=view(embedded);
    await api.setAdapter({
      async read(){return [copy(content),copy(frozen)];},
      async readRevision(){return copy(frozen);},
      async readChanges(){return copy(frozen);},
      async readJobs(){return {jobs:[]};},
      async update(){throw Error('历史快照只读。');}
    });
    window.reviewAnnotationDelivery=Object.freeze({backup:async()=>copy(embedded)});
    window.reviewAnnotationDeliveryReady=api.isReady();
    document.getElementById('annotation-status').textContent=`历史快照只读 · ${frozen.threads.length} 条批注`;
    return;
  }
  function associate(t,a,method,now) {
    const signature=x=>stable([x.quote,x.unit_text,x.raw_latex,x.atoms.map(a=>[a.offset,a.raw_latex])]);
    if(signature(t.current_anchor)!==signature(a))t.history.push({at:now,method,anchor:copy(a)});
    t.current_anchor=copy(a);t.updated_at=now;
  }
  function dispatch(data,e) {
    keys(e,['project_id','version_id','request']);
    const r=e.request;if(['suggestion_preview','suggestion_apply'].includes(r.operation))fail('当前报告没有本机源码服务。建议已保留，无法回写源码。');const fields={create:['selection','body','author','category'],reply:['thread_id','body','author','category'],edit:['thread_id','message_id','body','category'],delete_message:['thread_id','message_id'],delete_thread:['thread_id'],resolve:['thread_id'],reopen:['thread_id'],update_metadata:['thread_id'],associate:['thread_id','selection'],reconcile:[],import:['bundle']};
    if(!Object.hasOwn(fields,r.operation))fail('未知批注操作');keys(r,['operation','expected_revision','request_id',...fields[r.operation],...(r.operation==='reply'?['reply_to']:[]),...(['create','reply'].includes(r.operation)?['suggested_replacement']:[]),...(['create','update_metadata'].includes(r.operation)?['title','tags']:[])],['operation','expected_revision',...fields[r.operation]]);
    if(e.project_id!==content.project_id||e.version_id!==content.version_id)fail('请求项目或版本不一致');
    if(r.expected_revision!==data.revision)fail('批注已被其他窗口更新。输入已保留。');
    if(r.operation==='update_metadata'&&!('title' in r)&&!('tags' in r))fail('更新元数据需要 title 或 tags');
    const now=new Date().toISOString();let result=null;
    const message=()=>{messageFields(r);if('suggested_replacement' in r)validateSuggestion(r.suggested_replacement);return {...(r.suggested_replacement?{suggested_replacement:{original_chunk:r.suggested_replacement.original_chunk,replacement_chunk:r.suggested_replacement.replacement_chunk,status:'pending',source_span:null,reason:'离线建议需要本机重新定位源码'}}:{}),id:uuid(),body:r.body,author:copy(r.author),category:r.category,created_at:now,updated_at:now,deleted_at:null,edits:[]};};
    if(r.operation==='create') {
      const a=anchor(content,r.selection),m=message(),id=uuid();
      data.threads[id]={id,project_id:data.project_id,created_version_id:content.version_id,created_at:now,updated_at:now,resolved:false,deleted_at:null,messages:[m],original_anchor:copy(a),current_anchor:copy(a),associations:{[content.version_id]:{status:'exact',anchor:a,candidates:[],reason:null}},history:[{at:now,method:'created',anchor:copy(a)}],...metadata(r)};result={thread_id:id,message_id:m.id};
    } else if(r.operation==='import')result=importBundle(data,r.bundle);
    else if(r.operation==='reconcile') {for(const t of Object.values(data.threads))if(!t.deleted_at&&!t.associations[content.version_id]){const a=locate(content,t.current_anchor);t.associations[content.version_id]=a;if(a.anchor)associate(t,a.anchor,a.status,now);}}
    else {
      const t=data.threads[r.thread_id];if(!t||t.deleted_at)fail('线程已删除；输入已保留');
      if(r.operation==='reply'){const parent=r.reply_to??t.messages[0].id;if(!t.messages.some(m=>m.id===parent&&!m.deleted_at))fail('回复对象不存在或已删除');const m=message();m.reply_to=parent;t.messages.push(m);result={message_id:m.id};}
      else if(r.operation==='edit'||r.operation==='delete_message') {
        const m=t.messages.find(m=>m.id===r.message_id);if(!m||m.deleted_at)fail('内容已删除；输入已保留');
        if(r.operation==='edit'){text(r.body,'批注正文');category(r.category);m.edits.push({body:m.body,category:m.category,at:now});m.body=r.body;m.category=r.category;m.updated_at=now;}else m.deleted_at=now;
      } else if(r.operation==='update_metadata'){Object.assign(t,metadata(r));result={thread_id:t.id};}
      else if(r.operation==='delete_thread')t.deleted_at=now;
      else if(r.operation==='resolve'||r.operation==='reopen')t.resolved=r.operation==='resolve';
      else if(r.operation==='associate'){const a=anchor(content,r.selection);t.associations[content.version_id]={status:'manual',anchor:a,candidates:[],reason:null};associate(t,a,'manual',now);}
      else fail('未知批注操作');t.updated_at=now;
    }
    data.revision++;return {...view(data),result};
  }
  let dbPromise;
  async function transaction(fn,write=false) {
    dbPromise||=new Promise((resolve,reject)=>{const r=indexedDB.open('latex-review-annotations',1);r.onupgradeneeded=()=>r.result.createObjectStore('projects');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
    const db=await dbPromise;
    return new Promise((resolve,reject)=>{
      const tx=db.transaction('projects',write?'readwrite':'readonly'),store=tx.objectStore('projects'),r=store.get(content.project_id);let result,error;
      r.onsuccess=()=>{try {const data=codec.decodeVersions(r.result)||copy(embedded);validateBundle(data);validateContent(content);const existing=data.versions[content.version_id];if(existing&&stable(existing.units.map(unitSemantic))!==stable(content.units.map(unitSemantic)))fail('同版本正文坐标不一致');data.versions[content.version_id]=copy(content);result=fn(data,Boolean(r.result));if(write)store.put(codec.encodeStorage(data),content.project_id);}catch(e){error=e;tx.abort();}};
      tx.oncomplete=()=>resolve(result);tx.onabort=tx.onerror=()=>reject(error||tx.error);
    });
  }
  let local=false,token=null,initialSnapshot=null;
  async function json(url,options) {
    const r=await fetch(url,options);
    if(!r.ok) {
      let value;try{value=await r.json();}catch(_){}
      const error=new Error(value?.message||value?.error||'批注接口失败');error.status=r.status;throw error;
    }
    return r.json();
  }
  async function encodeRequest(envelope) {
    const body=JSON.stringify(envelope);
    if(body.length>16384&&typeof CompressionStream!=='undefined'){
      const stream=new Blob([body]).stream().pipeThrough(new CompressionStream('gzip'));
      return {body:await new Response(stream).arrayBuffer(),headers:{'Content-Encoding':'gzip'}};
    }
    return {body,headers:{}};
  }
  function restoreRequestBodies(value, request) {
    const bodies=request.operation==='batch'?new Map(request.items.map(item=>[item.id,item.body])):new Map([['single',request.body]]);
    for(const thread of value.threads||[])for(const message of thread.messages){
      if(!Object.hasOwn(message,'body_ref'))continue;
      const body=bodies.get(message.body_ref);if(typeof body!=='string')throw Error('批注响应缺少正文引用');
      message.body=body;delete message.body_ref;
    }
    return value;
  }
  const adapter={
    async readRevision(){if(local)return transaction(d=>({project_id:d.project_id,version_id:content.version_id,revision:d.revision}));return json('api/annotations/status?view=revision');},
    async readChanges(since){return local?transaction(view):json('api/annotations/status?view=changes&since='+since);},
    async readJobs(){return local?{jobs:[]}:json('api/annotations/jobs');},
    async readStatus(){if(local)return transaction(view);const s=await json('api/annotations/status?view=page');token=s.token;return s;},
    // Content belongs to this page version; every snapshot is checked against it by the UI.
    async read(){const s=initialSnapshot||await adapter.readStatus();initialSnapshot=null;return [content,s];},
    async update(e,progress){
      if(local)return transaction(d=>dispatch(d,e),true);
      const slow=['suggestion_preview','suggestion_apply','import','reconcile'].includes(e.request.operation);
      const payload=await encodeRequest(e);
      const task=await json('api/annotations/update'+(slow?'?async=1':''),{method:'POST',headers:{'Content-Type':'application/json','X-LaTeX-Review-Token':token,'X-LaTeX-Review-Delta-Encoding':'request-bodies',...payload.headers},body:payload.body});
      if(!task.task_id)return restoreRequestBodies(task,e.request);
      // Keep polling under this report's proxy prefix, just like the submission.
      const statusURL='api/annotations/jobs/'+encodeURIComponent(task.task_id);
      while(true){const job=await json(statusURL);if(job.state==='complete')return job.result;if(job.state==='failed')fail(job.message||'批注任务失败');progress?.(job);await new Promise(resolve=>setTimeout(resolve,300));}
    }
  };
  async function backup(){return local?transaction(copy):json('api/annotations/export');}
  function download(value,name,type){const url=URL.createObjectURL(new Blob([value],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  const controls=document.createElement('div');controls.className='annotation-portable-tools';const delivery=document.createElement('details');delivery.className='annotation-delivery';const summary=document.createElement('summary');summary.textContent='导入与导出';delivery.append(summary,controls);
  const notice=document.createElement('p');notice.setAttribute('role','status');
  const add=(label,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=()=>Promise.resolve().then(fn).catch(e=>notice.textContent=e.message);controls.append(b);};
  if(embedded)add('导出报告原备份',()=>download(JSON.stringify(embedded,null,2),'annotations-original.json','application/json'));
  add('导出批注 JSON',async()=>download(JSON.stringify(await backup(),null,2),'annotations.json','application/json'));
  const input=document.createElement('input');input.type='file';input.accept='.json,application/json';input.hidden=true;
  add('导入批注 JSON',()=>input.click());input.onchange=async()=>{try{const b=JSON.parse(await input.files[0].text());validateBundle(b);if(await api.update({operation:'import',bundle:b}))notice.textContent='批注备份已导入。';else notice.textContent=document.getElementById('annotation-status').textContent;}catch(e){notice.textContent=e.message;}finally{input.value='';}};
  const published=document.querySelector('meta[name="latex-review-publication"]')?.content==='static';
  async function exportHTML() {
    if(published)fail('在线报告不提供独立 HTML 导出。请使用本机报告导出。');
    await window.reviewMath?.complete();
    const b=await backup(),clone=document.documentElement.cloneNode(true);
    let storage=clone.querySelector('meta[name="latex-review-annotation-storage"]');
    if(!storage){storage=document.createElement('meta');storage.name='latex-review-annotation-storage';clone.querySelector('head').append(storage);}storage.content='browser';
    clone.querySelector('#annotation-current-version')?.remove();
    clone.querySelector('#annotation-backup-gzip')?.remove();
    clone.querySelectorAll('.review-print-warning,.annotation-overview,.annotation-editor,.annotation-toolbar,.annotation-card,.annotation-mark,.annotation-margin,.annotation-drafts').forEach(e=>e.remove());
    clone.querySelectorAll('.inline-change,.change-anchor,.translation-block,.translation-toggle').forEach(e=>e.remove());
    clone.querySelectorAll('.change-trigger').forEach(e=>{e.classList.remove('change-trigger');for(const a of ['role','tabindex','aria-controls','aria-label','title'])e.removeAttribute(a);});
    clone.querySelectorAll('.annotation-active-formula,.annotation-located-formula').forEach(e=>e.classList.remove('annotation-active-formula','annotation-located-formula'));
    clone.querySelectorAll('.math-tex[data-annotation-latex],.math-tex[data-review-math-source]').forEach(e=>{e.replaceChildren(document.createTextNode(e.dataset.annotationMathSource||e.dataset.annotationLatex||e.dataset.reviewMathSource));e.classList.remove('preview-unavailable');delete e.dataset.renderError;e.style.visibility='';});
    clone.querySelectorAll('mjx-container,mjx-assistive-mml,style[id^=MJX]').forEach(e=>e.remove());
    clone.querySelectorAll('script[src]').forEach(e=>{if(/mathjax/i.test(e.src)&&!/tex-chtml\.js/.test(e.src))e.remove();});
    clone.querySelectorAll('#live-sections option:not(:first-child),#live-results li').forEach(e=>e.remove());
    clone.querySelectorAll('[style]').forEach(e=>{for(const p of ['--change-left','--change-top','--margin-overflow'])e.style.removeProperty(p);});
    for(const [id,value] of [['annotation-content',api.getContent()],['annotation-backup',b]]){
      let tag=clone.querySelector('#'+id);if(!tag){tag=document.createElement('script');tag.type='application/json';tag.id=id;clone.querySelector('head').append(tag);}tag.textContent=JSON.stringify(value).replace(/</g,'\\u003c');
    }
    clone.querySelector('body').classList.remove('annotation-panel-open');
    const assets=readJSON('annotation-assets')||{};
    if(!local){
      const names=new Set();
      clone.querySelectorAll('[src],[href],[data-fallback-src]').forEach(e=>{for(const attr of ['src','href','data-fallback-src']){const value=e.getAttribute(attr);if(value&&/^(assets|previews|pages)\//.test(value))names.add(value.split(/[?#]/)[0]);}});
      let total=0;
      for(const name of names){
        const response=await fetch(name);if(!response.ok)fail('资源读取失败：'+name);
        const blob=await response.blob();total+=blob.size;if(blob.size>25*1024*1024||total>100*1024*1024)fail('单文件资源超出内嵌上限');
        assets[decodeURIComponent(name)]=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(reader.error);reader.readAsDataURL(blob);});
      }
    }
    clone.querySelectorAll('[src],[href]').forEach(e=>{for(const attr of ['src','href']){const value=e.getAttribute(attr);if(!value||/^(data:|https?:|#)/.test(value))continue;const embeddedAsset=assets[decodeURIComponent(value.split(/[?#]/)[0])];if(embeddedAsset)e.setAttribute(attr,embeddedAsset);}});
    clone.querySelectorAll('[data-fallback-src]').forEach(e=>{const embeddedAsset=assets[e.dataset.fallbackSrc];if(embeddedAsset)e.dataset.fallbackSrc=embeddedAsset;});
    for(const image of clone.querySelectorAll('img[src]')) {
      if(/^(data:|https?:)/.test(image.getAttribute('src')))continue;
      const embeddedAsset=readJSON('annotation-assets')?.[image.getAttribute('src')];if(embeddedAsset){image.src=embeddedAsset;continue;}
      const original=[...document.images].find(i=>i.getAttribute('src')===image.getAttribute('src'));if(!original?.complete||!original.naturalWidth)fail('本地图片尚未加载，无法导出');
      const canvas=document.createElement('canvas');canvas.width=original.naturalWidth;canvas.height=original.naturalHeight;canvas.getContext('2d').drawImage(original,0,0);image.src=canvas.toDataURL('image/png');
    }
    download('<!doctype html>\n'+clone.outerHTML,'report-annotated.html','text/html');return '<!doctype html>\n'+clone.outerHTML;
  }
  if(!published)add('导出最新 HTML',exportHTML);controls.append(input,notice);document.querySelector('#annotation-overview').append(delivery);
  const draftKey='latex-review-drafts:'+content.project_id+':'+location.pathname;
  let draftsLoaded=false,lastDraftState=null;
  function saveDrafts(){if(!draftsLoaded)return;try{const drafts=api.getDrafts(),state=JSON.stringify(drafts);if(state===lastDraftState)return;if(!Object.keys(drafts).length)localStorage.removeItem(draftKey);else localStorage.setItem(draftKey,JSON.stringify({project_id:content.project_id,version_id:content.version_id,content:api.getContent()||content,drafts}));lastDraftState=state;}catch(e){notice.textContent='草稿存储失败：'+e.message;}}
  document.addEventListener('annotation-draft',saveDrafts);window.addEventListener('pagehide',saveDrafts);
  async function restoreDrafts() {
    let saved;try{saved=JSON.parse(localStorage.getItem(draftKey));}catch(_){}if(!saved||saved.project_id!==content.project_id){draftsLoaded=true;return;}
    const drafts=saved.drafts||{};
    for(const d of Object.values(drafts)) {
      if(d.operation==='create') {
        try {const a=d.anchor||anchor(d.content||saved.content,d.selection),match=saved.version_id===content.version_id?{anchor:anchor(content,d.selection)}:locate(content,a);d.anchor=a;d.original_content=saved.content;if(match.anchor)d.selection=selection(match.anchor);else{d.selection=null;d.warning='原选区已变化。输入已保留，请重新选择正文。';}}catch(e){d.selection=null;d.warning='原选区不可恢复。请重新选择正文。';}
      } else {
        const current=api.getSnapshot().threads.find(t=>t.id===d.thread?.id),m=current?.messages.find(m=>m.id===d.message?.id);
        if(!current||d.operation==='edit'&&(!m||m.deleted_at||stable(m)!==stable(d.message)))d.warning='操作对象已删除或被其他审阅者编辑。输入已保留。';
      }
    }
    api.restoreDrafts(drafts);draftsLoaded=true;
    const keys=Object.keys(drafts);if(keys.length){api.resumeDraft(keys.at(-1));notice.textContent='编辑草稿已恢复。';}
  }
  async function start() {
    const storage=document.querySelector('meta[name="latex-review-annotation-storage"]')?.content;
    if(storage!==undefined&&!['disk','browser'].includes(storage))fail('批注存储方式无效');
    local=storage==='browser'||storage===undefined&&(published||location.protocol==='file:');
    if(!local) {
      try {initialSnapshot=await adapter.readStatus();if(initialSnapshot.project_id!==content.project_id||initialSnapshot.version_id!==content.version_id)fail('页面与接口身份不一致');}
      catch(e) {
        // Unmarked historical HTTP reports may have been copied to a static host.
        if(storage!==undefined||!embedded||![404,405].includes(e.status))throw e;
        local=true;
      }
    }
    let embeddedConflict='';
    if(local)await transaction((data,existing)=>{try{const result=existing?importBundle(data,embedded):{imported:[]};if(result.imported.length)data.revision++;}catch(e){embeddedConflict=e.message+'。浏览器记录未覆盖；可导出报告原备份。';}return null;},true);
    await api.setAdapter(adapter);
    if(!api.isReady())return;
    // Only the current live page commits migrations. Opening historical reports is read-only.
    if(!local&&document.body.classList.contains('live-view')&&api.getContent())await api.update({operation:'reconcile'});
    await restoreDrafts();window.reviewAnnotationDeliveryReady=true;notice.textContent=embeddedConflict||(local?'批注保存在当前浏览器。请导出 HTML 或 JSON 以携带更新。':'批注保存到项目共享磁盘。');
  }
  window.reviewAnnotationDelivery=Object.freeze({backup,exportHTML,importBundle:async b=>{validateBundle(b);return api.update({operation:'import',bundle:b});},locate,anchor,saveDrafts});
  const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('latex-review-annotations:'+content.project_id):null;
  if(channel){channel.onmessage=()=>{if(local)api.refresh();};const update=adapter.update;adapter.update=async (e,progress)=>{const result=await update(e,progress);channel.postMessage('updated');return result;};}
  start().catch(e=>notice.textContent=e.message+' 输入已保留。');
})();
