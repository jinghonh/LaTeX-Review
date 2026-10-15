#!/usr/bin/env python3
"""Browser-to-painted-success benchmark using isolated report/data copies."""
import argparse, copy, json, math, os, re, shutil, statistics, tempfile, time
import psutil
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright
from latex_review.annotations import AnnotationStore, MANIFEST, load_content, _hash
from latex_review.annotations._storage import Database, encode
from latex_review.serve import create_server
from latex_review.version_storage import unpack_storage

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--report',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--scale',action='store_true',help='Seed 10000 threads and 100 versions')
p.add_argument('--samples',type=int,default=30)
p.add_argument('--modes',nargs='+',help='Run selected scenarios')
p.add_argument('--skip-concurrency',action='store_true')
p.add_argument('--browser',default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
args=p.parse_args()
source=load_content(args.report)
legacy=Path(source['project_root'])/'.latex-review/annotations'/f"{source['project_id']}.json"
backup=legacy if legacy.exists() else legacy.with_suffix('.json.migrated')
# Read only the original legacy backup. The benchmark never opens the real SQLite store.
original_bytes=backup.read_bytes()
results={}; errors=[]; process=psutil.Process()
with tempfile.TemporaryDirectory(prefix='annotation-browser-perf-') as temporary:
    root=Path(temporary)/'paper';root.mkdir();root=root.resolve();report=Path(temporary)/'report';report.mkdir()
    content=copy.deepcopy(source);content['project_root']=str(root)
    content['project_id']=_hash({'root':str(root),'entry':content['entry']})
    store=AnnotationStore.for_content(content)
    old_id=source['project_id'];new_id=content['project_id']
    bundle=unpack_storage(json.loads(original_bytes))
    bundle['project_id']=new_id
    for version in bundle['versions'].values():
        version.update(project_id=new_id,project_root=str(root))
    for thread in bundle['threads'].values():thread['project_id']=new_id
    store.directory.mkdir(parents=True)
    # Validate before importing, then migrate a complete logical bundle once.
    store._import({'versions':{},'threads':{}},{k:v for k,v in bundle.items() if k!='requests'})
    Database(store.path,new_id,content['schema_version']).initialize(bundle)
    seed_threads=len(bundle['threads']);del bundle
    unit=min((u for u in content['units'] if u['kind']=='text' and len(u['text'])>40),key=lambda u:len(u['text']))
    selection=dict(unit_id=unit['id'],start=0,end=20,side='new')
    create=dict(operation='create',selection=selection,body='性能验收意见',author=dict(name='Codex',type='agent'),category='note')
    if args.scale:
        with store._locked() as data:
            for i in range(100-len(data['versions'])):
                v=copy.deepcopy(content);v['version_id']=_hash({'benchmark_version':i});data['versions'][v['version_id']]=v
            seed_units=[u for u in content['units'] if u['kind']=='text' and len(u['text'])>40]
            for i in range(9900-seed_threads):
                selected=seed_units[i%len(seed_units)]
                store._apply(data,content,{**create,'selection':dict(unit_id=selected['id'],start=0,end=20,side='new')})
            store._persist(data)
        del data  # Release setup graphs before measuring HTTP requests.
    html=(args.report/'report.html').read_text().replace(old_id,new_id).replace(source['project_root'],str(root))
    package=Path(__file__).resolve().parents[1]/'src/latex_review'
    def update_script(match):
        text=match.group(2)
        for signature,name in [('window.reviewAnnotations=Object.freeze','report_annotations.js'),("indexedDB.open('latex-review-annotations'",'annotation_delivery.js')]:
            if signature in text:return match.group(1)+(package/name).read_text()+match.group(3)
        return match.group(0)
    html=re.sub(r'(<script(?:\s[^>]*)?>)(.*?)(</script>)',update_script,html,flags=re.S)
    (report/'report.html').write_text(html)
    (report/MANIFEST).write_text(encode(content))
    if (args.report/'live-status.json').exists():shutil.copy(args.report/'live-status.json',report/'live-status.json')
    for folder in ('assets','previews'):
        if (args.report/folder).exists():shutil.copytree(args.report/folder,report/folder)
    with create_server(report) as server,sync_playwright() as pw:
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True)
            page=browser.new_page(viewport=dict(width=1280,height=900))
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('https://**',lambda r:r.abort())
            cold=time.perf_counter();page.goto(f'http://127.0.0.1:{server.server_port}/report.html')
            page.wait_for_function('window.reviewAnnotationDeliveryReady === true && window.reviewAnnotations?.isReady() && !reviewAnnotations.isBusy()',timeout=180000)
            # The live report polls forever; its explicit ready signal is authoritative.
            page.wait_for_function('document.querySelectorAll(".annotation-card").length > 0')
            page.evaluate('reviewAnnotations.refresh()')
            page.wait_for_function('reviewAnnotations.isReady()')
            prep=time.perf_counter()-cold
            print(json.dumps({'ready_seconds':prep,'scale':args.scale}),flush=True)
            for mode,count in [('ui_create',1),('create',1),('reply',1),('edit',1),('resolve',1),('reopen',1),('update_metadata',1),('delete_message',1),('delete_thread',1),('batch',100),('batch_max_payload',100),('preflight',100),('preflight_max_payload',100),('batch_reply',100),('batch_edit',100),('batch_resolve',100),('batch_reopen',100),('batch_update_metadata',100),('batch_delete_message',100),('batch_delete_thread',100)]:
                if args.modes and mode not in args.modes:continue
                cpu0=time.process_time(); rss0=process.memory_info().rss
                samples=page.evaluate('''async ({mode,count,samples,create})=>{
                  const rows=[];window.__annotationRequests=[];window.__annotationServerTimings=[];const originalFetch=window.__originalBenchmarkFetch||(window.__originalBenchmarkFetch=window.fetch);window.fetch=async(...args)=>{const start=performance.now(),r=await originalFetch(...args);if(String(args[0]).includes('/update')){window.__annotationRequests.push(performance.now()-start);window.__annotationServerTimings.push(r.headers.get('Server-Timing'));}return r;};
                  const seed=async request=>{const start=performance.now(),ok=await reviewAnnotations.update(request);await new Promise(r=>setTimeout(r,Math.max(0,200-(performance.now()-start))));return ok;};
                  for(let i=0;i<samples;i++){
                    let request,cleanup=[];
                    if(mode==='ui_create'){reviewAnnotations.openComposer('create',null,create.selection);document.querySelector('.annotation-editor > textarea').value=create.body;document.querySelector('.annotation-editor > textarea').dispatchEvent(new Event('input'));}
                    else if(mode==='create')request={...create};
                    else if(['batch','batch_max_payload','preflight','preflight_max_payload'].includes(mode))request={operation:'batch',request_id:crypto.randomUUID(),atomic:true,dry_run:mode.startsWith('preflight'),items:Array.from({length:count},(_,j)=>({...create,body:mode.endsWith('max_payload')?'x'.repeat(9500):create.body,id:String(j)}))};
                    else if(mode.startsWith('batch_')){
                      if(!await seed({operation:'batch',request_id:crypto.randomUUID(),atomic:true,items:Array.from({length:count},(_,j)=>({...create,id:String(j)}))}))throw Error('batch seed failed');
                      const targets=reviewAnnotations.getSnapshot().threads.slice(-count),operation=mode.slice(6);cleanup=targets.map(t=>t.id);
                      request={operation:'batch',request_id:crypto.randomUUID(),atomic:true,items:targets.map((t,j)=>{
                        const item={id:String(j),operation,thread_id:t.id};
                        if(operation==='reply')Object.assign(item,{body:'回复意见',author:create.author,category:'note'});
                        if(operation==='edit')Object.assign(item,{message_id:t.messages[0].id,body:'编辑意见',category:'note'});
                        if(operation==='delete_message')item.message_id=t.messages[0].id;
                        if(operation==='update_metadata')item.title='性能验收标题';return item;
                      })};
                    }
                    else{
                      if(!await seed({...create}))throw Error('seed failed');
                      const t=reviewAnnotations.getSnapshot().threads.at(-1),m=t.messages.at(-1);cleanup=[t.id];
                      request={operation:mode,thread_id:t.id};
                      if(mode==='reply')Object.assign(request,{body:'回复意见',author:create.author,category:'note'});
                      if(mode==='edit')Object.assign(request,{message_id:m.id,body:'编辑意见',category:'note'});
                      if(mode==='delete_message')request.message_id=m.id;
                      if(mode==='update_metadata')request.title='性能验收标题';
                    }
                    if(mode==='ui_create')await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
                    const start=performance.now();
                    let ok;
                    if(mode==='ui_create'){
                      const editor=document.querySelector('.annotation-editor');
                      const done=new Promise(resolve=>{const observer=new MutationObserver(()=>{if(editor.hidden){observer.disconnect();resolve(true);}});observer.observe(editor,{attributes:true});setTimeout(()=>{observer.disconnect();resolve(false);},10000);});
                      [...editor.querySelectorAll('button')].find(b=>b.textContent==='保存').click();ok=await done;
                    }else ok=await reviewAnnotations.update(request);
                    await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
                    const milliseconds=performance.now()-start;
                    if(!ok)throw Error(document.querySelector('#annotation-status').textContent);
                    rows.push(milliseconds);
                    if(mode==='create'||mode==='ui_create')cleanup=[reviewAnnotations.getSnapshot().threads.at(-1).id];
                    if(mode==='batch'||mode==='batch_max_payload')cleanup=reviewAnnotations.getSnapshot().threads.slice(-count).map(t=>t.id);
                    await new Promise(r=>setTimeout(r,Math.max(0,200-milliseconds)));
                    const live=new Set(reviewAnnotations.getSnapshot().threads.map(t=>t.id));cleanup=cleanup.filter(id=>live.has(id));
                    if(cleanup.length&&!await seed({operation:'batch',request_id:crypto.randomUUID(),atomic:true,items:cleanup.map((id,j)=>({id:String(j),operation:'delete_thread',thread_id:id}))}))throw Error('cleanup failed');
                  }
                  return rows;
                }''',dict(mode=mode,count=count,samples=args.samples,create=create))
                ordered=sorted(samples);p95=ordered[math.ceil(.95*len(ordered))-1]
                network=page.evaluate('window.__annotationRequests')
                results[mode]=dict(server_timings=page.evaluate('window.__annotationServerTimings'),cpu_seconds=time.process_time()-cpu0,rss_start_mib=rss0/2**20,rss_end_mib=process.memory_info().rss/2**20,network_ms=network,count=count,samples_ms=samples,median_ms=statistics.median(samples),p95_ms=p95,max_ms=max(samples),passed=p95<=200)
                print(json.dumps({'mode':mode,**{k:v for k,v in results[mode].items() if k not in ('samples_ms','network_ms','server_timings')}}),flush=True)
            if not args.skip_concurrency:
                # Three pages share one project. Submit at 5 Hz with current revision,
                # and validate deliberate simultaneous stale writes separately.
                peers=[page]
                for _ in range(2):
                    peer=browser.new_page(viewport=dict(width=1280,height=900));peer.bring_to_front();peer.route('https://**',lambda r:r.abort())
                    peer.goto(page.url);peer.wait_for_function('window.reviewAnnotationDeliveryReady && reviewAnnotations.isReady()',timeout=180000)
                    peers.append(peer)
                for scenario,count in [('three_clients_5hz',1),('three_clients_batch_5hz',100),('three_clients_payload_5hz',100)]:
                    concurrent=[]
                    for i in range(args.samples):
                        peer=peers[i%3];peer.bring_to_front();peer.evaluate('reviewAnnotations.synchronize()')
                        revision=store.metadata(content)['revision']
                        measured=peer.evaluate("""async ({revision,create,count,scenario})=>{
                            if(reviewAnnotations.getSnapshot().revision!==revision)throw Error('revision synchronization failed');
                            let request=create;
                            if(count>1)request={operation:'batch',request_id:crypto.randomUUID(),atomic:true,items:Array.from({length:count},(_,j)=>({...create,id:String(j),body:scenario.includes('payload')?'x'.repeat(9500):create.body}))};
                            const t=performance.now(),ok=await reviewAnnotations.update(request);
                            await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
                            if(!ok)throw Error(document.querySelector('#annotation-status').textContent);
                            return performance.now()-t;
                        }""",dict(revision=revision,create=create,count=count,scenario=scenario))
                        concurrent.append(measured);page.wait_for_timeout(max(0,200-measured))
                        ids=peer.evaluate('count=>reviewAnnotations.getSnapshot().threads.slice(-count).map(t=>t.id)',count)
                        start=time.perf_counter()
                        assert peer.evaluate("""ids=>reviewAnnotations.update({operation:'batch',atomic:true,items:ids.map((id,j)=>({id:String(j),operation:'delete_thread',thread_id:id}))})""",ids)
                        page.wait_for_timeout(max(0,200-(time.perf_counter()-start)*1000))
                    concurrent.sort();results[scenario]=dict(count=count,samples_ms=concurrent,
                        median_ms=statistics.median(concurrent),p95_ms=concurrent[math.ceil(.95*len(concurrent))-1],
                        max_ms=max(concurrent),passed=concurrent[math.ceil(.95*len(concurrent))-1]<=200)
                    print(json.dumps({'mode':scenario,**{k:v for k,v in results[scenario].items() if k!='samples_ms'}}),flush=True)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(args.output.with_suffix('.png')))
            browser.close()
        finally:server.shutdown();worker.join()
    assert backup.read_bytes()==original_bytes
    data=dict(scale=args.scale,preparation_seconds=prep,logical_threads=9900 if args.scale else seed_threads,peak_active_threads=10000 if args.scale else None,versions=100 if args.scale else 33,results=results,page_errors=errors)
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    assert not errors,errors
    assert all(r['passed'] for r in results.values()),'P95 target failed; see output'
