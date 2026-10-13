(function(){
  'use strict';
  window.MathJax={loader:{load:['[tex]/boldsymbol']},startup:{typeset:false},tex:{packages:{'[+]':['ams','newcommand','boldsymbol']}}};
  const status=document.getElementById('math-status'),states=new WeakMap();
  let engine=false,failed=false,running=null,errors=0;
  window.reviewRenderDiagnostics=[];
  const pause=()=>new Promise(resolve=>setTimeout(resolve,0));
  function source(node){if(!node.querySelector('mjx-container,img')&&!node.dataset.renderError)return node.textContent;return node.dataset.reviewMathSource||node.dataset.annotationMathSource||node.dataset.annotationLatex||node.textContent;}
  function unavailable(node,reason){
    const original=source(node);node.dataset.reviewMathSource=original;
    if(node.dataset.fallbackSrc){const image=document.createElement('img');image.src=node.dataset.fallbackSrc;image.alt='公式预览';image.className='compiled-fragment';node.replaceChildren(image);}
    else {node.textContent=original;node.classList.add('preview-unavailable');node.title='此处暂无法预览；保留公式原文。';}
    node.style.visibility='visible';node.dataset.renderError=String(reason);
    window.reviewRenderDiagnostics.push({kind:'math',reason:String(reason)});
  }
  function pending(){return [...document.querySelectorAll('.math-tex')].filter(node=>!states.has(node)&&!node.querySelector('mjx-container'));}
  function update(){status.dataset.state=failed?'failed':errors?'partial':'ready';status.textContent=failed?'在线公式排版不可用；保留公式原文。':errors?'部分公式暂无法预览；保留公式原文。':'公式排版完成。';window.dispatchEvent(new Event('review-math-ready'));}
  async function drain(){
    if(running)return running;
    if(!engine&&!failed)return;
    running=(async()=>{
      await Promise.resolve();
      status.dataset.state='rendering';status.textContent='公式正在分批排版。';
      let nodes=pending(),index=0;
      while(index<nodes.length){
        const start=performance.now();let count=0;
        while(index<nodes.length){
          const node=nodes[index++];
          if(!node.isConnected||states.has(node))continue;
          const latex=source(node),before=node.textContent;node.dataset.reviewMathSource=latex;states.set(node,'running');
          try{
            if(failed)throw Error('公式引擎不可用');
            const rendered=await MathJax.tex2chtmlPromise(latex,{display:node.dataset.display==='true'});
            if(rendered.querySelector('mjx-merror,[data-mjx-error],mtext[mathcolor="red"],mjx-mtext[style*="color: red"]'))throw Error('公式语法或命令不受支持');
            if(node.isConnected&&node.textContent!==before){states.delete(node);delete node.dataset.reviewMathSource;continue;}
            if(node.isConnected){node.replaceChildren(rendered);node.style.visibility='visible';node.classList.remove('preview-unavailable');delete node.dataset.renderError;}
          }catch(error){errors++;if(node.isConnected)unavailable(node,error.message||error);}
          states.set(node,'done');
          if(++count>=(failed?64:24)||performance.now()-start>=12)break;
        }
        await pause();if(index>=nodes.length){nodes=pending();index=0;}
      }
      update();
    })().finally(()=>{running=null;});
    return running;
  }
  function dependencyFailed(){if(engine||failed)return;failed=true;clearTimeout(timer);drain();}
  const timer=setTimeout(dependencyFailed,10000);
  window.mathDependencyFailed=dependencyFailed;
  window.mathDependencyReady=function(){
    if(!window.MathJax?.startup?.promise){dependencyFailed();return;}
    MathJax.startup.promise.then(()=>{if(failed)return;if(!MathJax.tex2chtmlPromise){dependencyFailed();return;}clearTimeout(timer);engine=true;drain();},dependencyFailed);
  };
  // All source nodes stay in the DOM. Search, selection and browser find remain available.
  const observer=new MutationObserver(records=>{
    let changed=false;
    for(const record of records){
      const node=(record.target.nodeType===1?record.target:record.target.parentElement)?.closest('.math-tex');
      if(node&&states.get(node)==='done'&&!node.querySelector('mjx-container,img')&&node.textContent!==node.dataset.reviewMathSource){states.delete(node);delete node.dataset.reviewMathSource;delete node.dataset.renderError;changed=true;}
      for(const added of record.addedNodes||[]){if(added.nodeType===1&&(added.matches('.math-tex')||added.querySelector('.math-tex')))changed=true;}
    }
    if(changed&&!running)drain();
  });
  observer.observe(document.body,{childList:true,subtree:true,characterData:true});
  async function complete(){
    if(!engine&&!failed)await new Promise(resolve=>{const poll=()=>engine||failed?resolve():setTimeout(poll,25);poll();});
    do {await drain();}while(pending().length);
    if(document.querySelector('.math-tex:not([data-review-math-source]):not(:has(mjx-container))'))throw Error('公式排版未完成');
    return {failed,errors,total:document.querySelectorAll('.math-tex').length};
  }
  window.reviewMath=Object.freeze({complete,enqueue:drain});
  const originalPrint=window.print.bind(window);
  window.print=async()=>{await complete();originalPrint();};
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='p'){event.preventDefault();window.print().catch(e=>{status.textContent=e.message;});}},true);
  window.addEventListener('beforeprint',()=>{if(running||(!engine&&!failed)||pending().length){status.textContent='公式排版尚未完成；未完成公式保留原文。';status.dataset.state='printing-source';const warning=document.createElement('p');warning.className='review-print-warning';warning.textContent=status.textContent;document.body.prepend(warning);}});
  window.addEventListener('afterprint',()=>document.querySelectorAll('.review-print-warning').forEach(node=>node.remove()));
})();
