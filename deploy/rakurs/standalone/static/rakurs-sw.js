"use strict";
// Network-only navigation. No CacheStorage, no storage of HTML, API results,
// credentials or session data. Offline mode never displays old readings.
self.addEventListener("install",event=>event.waitUntil(self.skipWaiting()));
self.addEventListener("activate",event=>event.waitUntil(self.clients.claim()));
self.addEventListener("fetch",event=>{
  if(event.request.method!=="GET"||event.request.mode!=="navigate"||new URL(event.request.url).origin!==self.location.origin)return;
  event.respondWith(fetch(event.request,{cache:"no-store"}).catch(()=>new Response(
    '<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RAKURS — немає зв’язку</title><h1>RAKURS</h1><p>Немає мережевого з’єднання. Показники не завантажено.</p><p><a href="/">Повторити підключення</a></p></html>',
    {status:503,headers:{"Content-Type":"text/html; charset=utf-8","Cache-Control":"no-store","Content-Security-Policy":"default-src 'none'; base-uri 'none'; frame-ancestors 'none'"}})));
});
