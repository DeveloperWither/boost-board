// Cache-first: opens instantly and works fully offline; updates quietly in the background.
const C='bb-v3';
const F=['./','index.html','manifest.webmanifest','icon-180.png','icon-192.png','icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(F)));self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{
  const r=e.request;if(r.method!=='GET')return;
  const u=new URL(r.url);
  const ok=u.origin===location.origin||/fonts\.(googleapis|gstatic)\.com$/.test(u.hostname);
  if(!ok)return;
  e.respondWith(caches.open(C).then(async c=>{
    const key=r.mode==='navigate'?'index.html':r;
    const hit=await c.match(key,{ignoreSearch:true});
    const net=fetch(r).then(res=>{if(res&&(res.ok||res.type==='opaque'))c.put(key,res.clone());return res}).catch(()=>hit);
    return hit||net;
  }));
});
