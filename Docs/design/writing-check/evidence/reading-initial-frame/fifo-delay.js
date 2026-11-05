(() => {
  const nativeRAF = window.requestAnimationFrame.bind(window);
  const nativeCancel = window.cancelAnimationFrame.bind(window);
  const pending = new Map();
  let nextId=1, holding=true;
  const trace=[];
  function state() {
    const p=document.querySelector('.preview-side[data-side="new"]');
    const anchor=window.testAnchors?.new;
    const rect=anchor?.range.getBoundingClientRect();
    return {t:performance.now(),width:innerWidth,height:innerHeight,scrollY,
      resizing:window.reviewReadingPosition?.isResizing(),
      paper:p?{width:p.clientWidth,height:p.clientHeight,scrollTop:p.scrollTop,scrollHeight:p.scrollHeight,
        overflowY:getComputedStyle(p).overflowY}:null,
      savedRange:anchor?{top:rect.top,bottom:rect.bottom,ratio:anchor.ratio,character:anchor.range.toString(),offset:anchor.range.startOffset}:null};
  }
  function dispatch(id, item) {
    item.native=nativeRAF(timestamp=>{
      if(!pending.has(id))return;
      pending.delete(id);
      trace.push({kind:'callback-before',id,name:item.callback.name,source:item.callback.toString().slice(0,160),...state()});
      item.callback(timestamp);
      trace.push({kind:'callback-after',id,name:item.callback.name,...state()});
    });
  }
  window.requestAnimationFrame=callback=>{
    const id=nextId++,item={callback,native:null};pending.set(id,item);
    trace.push({kind:'registered',id,name:callback.name,source:callback.toString().slice(0,160),t:performance.now()});
    if(!holding)dispatch(id,item);
    return id;
  };
  window.cancelAnimationFrame=id=>{
    const item=pending.get(id);if(!item)return;
    pending.delete(id);if(item.native!==null)nativeCancel(item.native);
    trace.push({kind:'cancelled',id,name:item.callback.name,t:performance.now()});
  };
  window.__initialRAFProbe={trace,state,release(){
    trace.push({kind:'release-original-FIFO',pending:[...pending].map(([id,item])=>({id,name:item.callback.name})),...state()});
    holding=false;for(const [id,item] of pending)if(item.native===null)dispatch(id,item);
  },pending(){return [...pending].map(([id,item])=>({id,name:item.callback.name,source:item.callback.toString().slice(0,160)}));}};
  for(const type of ['resize','scroll','load','review-math-ready'])window.addEventListener(type,()=>trace.push({kind:'native-event',type,...state()}),{passive:true});
})();