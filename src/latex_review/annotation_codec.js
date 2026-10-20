/* Historical annotation encodings enter through this codec; callers use complete bundles. */
(() => {
  'use strict';
  const maxBytes=200*1024*1024;
  const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);

  function decodeVersions(value) {
    if(!value||!('encoding' in value)) {
      // IndexedDB preserves shared references. Public bundles need independent units.
      const seen=new WeakSet();
      if(value?.versions&&typeof value.versions==='object'&&!Array.isArray(value.versions))for(const c of Object.values(value.versions))if(Array.isArray(c?.units))c.units=c.units.map(u=>{if(u&&typeof u==='object'){if(seen.has(u))return structuredClone(u);seen.add(u);}return u;});
      return value;
    }
    if(value.encoding!=='shared-units-v1'||Object.keys(value).sort().join(',')!=='bundle,encoding,units'||!Array.isArray(value.units)||value.units.some(u=>!u||typeof u!=='object'||Array.isArray(u))||!value.bundle?.versions||typeof value.bundle.versions!=='object'||Array.isArray(value.bundle.versions))throw Error('正文共享表示无效');
    const result=structuredClone(value.bundle),encoder=new TextEncoder();
    const sizes=value.units.map(u=>encoder.encode(JSON.stringify(u)).length);let expanded=encoder.encode(JSON.stringify(value.bundle)).length;
    for(const c of Object.values(result.versions)) {
      if(!Array.isArray(c.units)||c.units.some(i=>!Number.isInteger(i)||i<0||i>=value.units.length))throw Error('正文单元引用无效');
      expanded+=c.units.reduce((n,i)=>n+sizes[i],0);if(expanded>maxBytes)throw Error('恢复正文超过 200 MiB 上限');
      c.units=c.units.map(i=>structuredClone(value.units[i]));
    }
    return result;
  }

  function encodeStorage(value) {
    // Keep the complete shape readable by historical pages, sharing identical units in IndexedDB.
    const bundle={...value,versions:{}},indices=new Map(),encoder=new TextEncoder();let expanded=0;
    for(const [id,c] of Object.entries(value.versions)) {
      const units=c.units.map(u=>{const key=stable(u);expanded+=encoder.encode(key).length;if(expanded>maxBytes)throw Error('恢复正文超过 200 MiB 上限');if(!indices.has(key))indices.set(key,u);return indices.get(key);});
      expanded+=Math.max(0,units.length-1);bundle.versions[id]={...c,units};
    }
    const metadata={...bundle,versions:Object.fromEntries(Object.entries(bundle.versions).map(([id,c])=>[id,{...c,units:[]}]))};
    expanded+=encoder.encode(JSON.stringify(metadata)).length;if(expanded>maxBytes)throw Error('恢复正文超过 200 MiB 上限');
    return bundle;
  }

  async function decodeBackup(envelope) {
    if(!envelope||Object.keys(envelope).sort().join(',')!=='data,encoding,size'||!['gzip-base64-v1','gzip-shared-units-v1'].includes(envelope.encoding)||!Number.isInteger(envelope.size)||envelope.size<0||envelope.size>maxBytes)throw Error('压缩备份格式无效');
    if(typeof DecompressionStream==='undefined')throw Error('浏览器不支持 gzip 批注，请使用新版 Chrome');
    const bytes=Uint8Array.from(atob(envelope.data),c=>c.charCodeAt(0));
    const reader=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip')).getReader();
    const chunks=[];let size=0;
    while(true) {const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>envelope.size){await reader.cancel();throw Error('压缩备份大小不一致');}chunks.push(value);}
    if(size!==envelope.size)throw Error('压缩备份大小不一致');
    const bundle=decodeVersions(JSON.parse(await new Blob(chunks).text()));
    if(!bundle||typeof bundle!=='object'||Array.isArray(bundle)||!bundle.versions||!bundle.threads)throw Error('压缩备份契约无效');
    return bundle;
  }

  return Object.freeze({decodeVersions,encodeStorage,decodeBackup});
})()
