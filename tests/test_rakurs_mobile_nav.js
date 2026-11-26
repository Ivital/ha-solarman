"use strict";
// Browser-free regression test for mobile-only navigation. No network or devices.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const root=path.join(__dirname,"../deploy/rakurs/standalone");
const html=fs.readFileSync(path.join(root,"templates/index.html"),"utf8");
const css=fs.readFileSync(path.join(root,"static/rakurs-mobile-nav-20261126.css"),"utf8");
const source=fs.readFileSync(path.join(root,"static/rakurs-mobile-nav-20261126.js"),"utf8");
const dashboard=fs.readFileSync(path.join(root,"static/app-parallel-twin-20261125.js"),"utf8");

class E {
  constructor(tag,doc){
    this.tagName=tag;this.doc=doc;this.childNodes=[];this.listeners={};
    this.attributes={};this.dataset={};this.className="";
    this.textContent="";this.hidden=false;this.value="";this.href="";
    const self=this;
    this.classList={
      toggle(cls,force){
        const set=new Set(self.className.split(/\s+/).filter(Boolean));
        if(force===undefined ? !set.has(cls) : force)set.add(cls);
        else set.delete(cls);
        self.className=Array.from(set).join(" ");
      },
      contains(cls){return self.className.split(/\s+/).includes(cls);}
    };
  }
  appendChild(e){this.childNodes.push(e);return e;}
  append(...elements){this.childNodes.push(...elements);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  removeAttribute(name){delete this.attributes[name];}
  addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);}
  trigger(name,event={}){for(const cb of this.listeners[name]||[])cb(event);}
  focus(){this.doc.activeElement=this;}
  querySelectorAll(query){
    const descendants=(node)=>node.childNodes.flatMap(child=>[child,...descendants(child)]);
    if(query==="a[data-route]")return descendants(this).filter(
      x=>x.tagName==="a"&&Boolean(x.dataset.route));
    throw Error("Unexpected selector "+query);
  }
}
const document={
  readyState:"complete",activeElement:null,listeners:{},
  elements:new Map(),body:null,
  createElement(tag){return new E(tag,this);},
  getElementById(id){return this.elements.get(id)||null;},
  addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);},
  trigger(name,event){for(const cb of this.listeners[name]||[])cb(event);}
};
document.body=new E("body",document);
const el=id=>{const item=document.createElement(id==="mobile-more"||id==="mobile-pages-close"?"button":"div");document.elements.set(id,item);return item;};
const tabs=el("mobile-tabs"),more=el("mobile-more"),panel=el("mobile-pages");
const overlay=el("mobile-pages-backdrop"),close=el("mobile-pages-close");
const input=el("mobile-pages-search"),list=el("mobile-page-list");
panel.hidden=true;overlay.hidden=true;
for(const route of ["overview","parallel","pv","history"]){
  const a=document.createElement("a");a.dataset.route=route;a.href="#/"+route;tabs.appendChild(a);
}
tabs.appendChild(more);
const events={};let scrolled=0,desktopChange=null;
const win={
  document,location:{hash:"#/overview"},
  addEventListener(name,cb){events[name]=cb;},
  scrollTo(){scrolled++;},
  matchMedia(){return {addEventListener(name,cb){if(name==="change")desktopChange=cb;}}}
};
vm.runInNewContext(source,{window:win},{filename:"rakurs-mobile-nav-20261126.js"});
const nav=win.RakursMobileNavigation;
assert(nav,"Dedicated mobile nav must initialize");
assert.equal(tabs.getAttribute("data-ready"),"true");
assert.equal(nav.routeOf("#/not-a-route"),"overview");
assert.equal(nav.routeOf("#/grid"),"grid");
const menuLinks=list.childNodes.flatMap(group=>group.childNodes.filter(item=>item.tagName==="a"));
const routes=nav.groups.flatMap(g=>g[1].map(item=>item[0]));
assert.equal(menuLinks.length,15,"All 15 existing pages must be accessible on mobile");
assert.equal(new Set(routes).size,15);
const dashboardRoutes=Array.from(
  dashboard.match(/const views = \[([\s\S]*?)\];/)[1].matchAll(/\["([^"]+)",/g),
  m=>m[1]);
assert.deepEqual([...routes].sort(),dashboardRoutes.sort(),
  "Mobile navigation must cover exactly the desktop router's routes");
assert.equal(tabs.childNodes.filter(e=>e.tagName==="a").length,4);
assert(tabs.childNodes[0].classList.contains("is-active"));
assert.equal(tabs.childNodes[0].getAttribute("aria-current"),"page");
assert(panel.hidden&&overlay.hidden);

document.activeElement=more;
more.trigger("click");
assert.equal(panel.hidden,false);
assert.equal(overlay.hidden,false);
assert.equal(more.getAttribute("aria-expanded"),"true");
assert(document.body.classList.contains("mobile-menu-open"));
assert.equal(document.activeElement,input,"Search must receive initial focus");
input.value="регістри";input.trigger("input");
assert.equal(menuLinks.filter(a=>!a.hidden).length,1);
assert.equal(menuLinks.find(a=>!a.hidden).dataset.route,"registers");
input.value="немає-такої-сторінки";input.trigger("input");
assert.equal(menuLinks.filter(a=>!a.hidden).length,0);
assert.equal(list.childNodes.at(-1).hidden,false,"Empty search state must be visible");
let prevented=false;
document.trigger("keydown",{key:"Escape",preventDefault(){prevented=true;}});
assert(prevented&&panel.hidden&&overlay.hidden);
assert.equal(more.getAttribute("aria-expanded"),"false");
assert(!document.body.classList.contains("mobile-menu-open"));
assert.equal(document.activeElement,more,"Escape restores focus");

more.trigger("click");
input.value="акум";input.trigger("input");
const battery=menuLinks.find(a=>a.dataset.route==="battery");
assert(!battery.hidden);
battery.trigger("click");
assert(panel.hidden,"Navigating from sheet must close it");
assert.equal(document.activeElement,more,"Choosing a route restores focus to visible navigation");
win.location.hash="#/battery";events.hashchange();
assert(more.classList.contains("is-active"),"Unpinned page activates More tab");
assert(more.getAttribute("aria-label").includes("Акумулятор"));
assert.equal(battery.getAttribute("aria-current"),"page");
assert(tabs.childNodes.slice(0,4).every(a=>a.getAttribute("aria-current")===null));
assert(scrolled>0,"Switching pages moves back to top");
win.location.hash="#/parallel";events.hashchange();
assert(tabs.childNodes[1].classList.contains("is-active"));
assert.equal(tabs.childNodes[1].getAttribute("aria-current"),"page");
assert(!more.classList.contains("is-active"));
assert.equal(battery.getAttribute("aria-current"),null);
console.log("MOBILE_NAV_15_ROUTES_AND_ACTIVE_TAB=PASS");

more.trigger("click");
overlay.trigger("click");
assert(panel.hidden && overlay.hidden);
more.trigger("click");
close.trigger("click");
assert(panel.hidden && overlay.hidden);
more.trigger("click");
const last=menuLinks.at(-1);
last.focus();let trapped=false;
document.trigger("keydown",{key:"Tab",shiftKey:false,preventDefault(){trapped=true;}});
assert(trapped&&document.activeElement===close,"Tab must wrap inside the open sheet");
close.focus();trapped=false;
document.trigger("keydown",{key:"Tab",shiftKey:true,preventDefault(){trapped=true;}});
assert(trapped&&document.activeElement===last,"Shift-Tab must wrap inside the sheet");
desktopChange({matches:true});
assert(panel.hidden,"Switching to desktop closes the mobile overlay");
console.log("MOBILE_NAV_SEARCH_SHEET_ESCAPE_FOCUS_TRAP=PASS");

nav.init();
assert.equal(menuLinks.length,15);
assert.equal(list.childNodes.filter(e=>e.className==="mobile-menu-group").length,3,
  "Repeated setup must not duplicate groups");
console.log("MOBILE_NAV_IDEMPOTENT=PASS");

for(const fragment of [
  'id="mobile-tabs"','id="mobile-more"','id="mobile-pages"',
  'id="mobile-pages-close"','id="mobile-pages-search"',
  'role="dialog" aria-modal="true"','aria-controls="mobile-pages"',
  '/static/rakurs-mobile-nav-20261126.js?v=rakurs-mobile-nav-v1',
  '/static/rakurs-mobile-nav-20261126.css?v=rakurs-mobile-nav-v1'
])assert(html.includes(fragment),"Missing HTML mobile navigation element: "+fragment);
for(const route of ["overview","parallel","pv","history"])
  assert(html.includes('data-route="'+route+'" href="#/'+route+'"'));
assert(html.indexOf("rakurs-mobile-nav-20261126.js")>html.indexOf("app-parallel-twin-20261125.js"),
  "Navigation must initialize after the main dashboard script");
assert(css.includes("@media(max-width:760px)")&&css.includes(".sidebar{display:none!important}"));
assert(css.includes("env(safe-area-inset-bottom"));
assert(css.includes("max-height:min(72dvh,650px)"));
assert(css.includes(".workarea")&&css.includes("padding-bottom:calc(76px"));
assert(css.includes(".mobile-tabs a.is-active"));
assert(css.includes("@media(min-width:761px)"));
assert(css.includes("prefers-reduced-motion"));
assert(!/\bfetch\s*\(|\bXMLHttpRequest\b|\bPOST\b/.test(source),
  "Navigation must not issue network writes");
console.log("MOBILE_NAV_RESPONSIVE_SAFE_AREA_READ_ONLY=PASS");
