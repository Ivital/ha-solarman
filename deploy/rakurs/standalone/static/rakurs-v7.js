"use strict";
/* Rakurs V7: data-driven SVG dispatch, read-only archive & engineering mode.
   No telemetry is fabricated; V7 is the canonical overview. */
(function(root){
 const M=root.RakursV7Model,NS="http://www.w3.org/2000/svg";
 let mode="overview",hours=24,chosenTime=null;
 const views=new Set(["pv1","pv2","load"]);
 const handlers=new WeakMap(),activeScopes=new Set();
 function disposeScope(scope){
  if(!scope||scope.cancelled)return;
  scope.cancelled=true;
  if(scope.onDispose)scope.onDispose();
  if(scope.dialogClose)scope.dialogClose();
  activeScopes.delete(scope);
 }
 const classes={pv1:"pv",pv2:"pv",inv1:"bus",inv2:"bus",bus:"bus",load:"load",battery:"battery",grid:"grid"};
 const labels={pv1:"PV · Deye №1",pv2:"PV · Deye №2",bus:"Спільні енергетичні вузли",inv1:"Deye №1",inv2:"Deye №2",
  load:"Будинок",battery:"Акумулятор",grid:"Мережа"};
 const metrics=["pv_power","load_power","battery_power","grid_power","battery_soc"];
 function h(name,cls,value){
  const e=document.createElement(name);if(cls)e.className=cls;
  if(value!==undefined)e.textContent=String(value);return e;
 }
 function s(name,attrs,value){
  const e=document.createElementNS(NS,name);
  for(const [k,v] of Object.entries(attrs||{}))e.setAttribute(k,String(v));
  if(value!==undefined)e.textContent=String(value);return e;
 }
 function put(parent,...children){for(const child of children)parent.appendChild(child);return parent;}
 function source(snap,id){
  if(id==="pv1")return snap.sources[0].reading;
  if(id==="pv2")return snap.sources[1].reading;
  if(id==="inv1"||id==="inv2")return null;
  if(id==="load")return snap.reports.load_power.reading;
  if(id==="grid")return snap.reports.grid_power.reading;
  if(id==="battery")return snap.reports.battery_soc.reading;
  return null;
 }
 function geometry(mobile){
  if(mobile)return {w:360,h:326,
   boxes:{pv1:[19,1,152,86],pv2:[189,1,152,86],inv1:[27,105,136,61],inv2:[197,105,136,61],
    bus:[137,184,86,26],load:[5,238,110,85],battery:[125,238,110,85],grid:[245,238,110,85]},
   paths:[["pv1","M95 87 V105"],["pv2","M265 87 V105"],
    ["inv1","M95 166 V174 Q95 180 103 180 H154 Q165 180 165 184"],
    ["inv2","M265 166 V174 Q265 180 257 180 H206 Q195 180 195 184"],
    ["bus","M180 210 V224 M60 224 H300"],["load","M60 224 V238"],
    ["battery","M180 224 V238"],["grid","M300 224 V238"]]};
  return {w:760,h:316,
   boxes:{pv1:[8,19,174,108],pv2:[8,188,174,108],inv1:[225,28,145,88],inv2:[225,197,145,88],
    bus:[404,135,78,46],load:[547,5,205,96],battery:[547,110,205,96],grid:[547,215,205,96]},
   paths:[["pv1","M182 73 H225"],["pv2","M182 242 H225"],
    ["inv1","M370 72 H384 Q392 72 392 80 V146 Q392 151 404 151"],
    ["inv2","M370 241 H384 Q392 241 392 233 V170 Q392 165 404 165"],
    ["bus","M482 158 H518 M518 53 V263"],["load","M518 53 H547"],
    ["battery","M518 158 H547"],["grid","M518 263 H547"]]};
 }
 // Native equipment artwork: vector paths, not emoji, screenshots or boxed glyphs.
 function equipment(group,id,x,y,scale=1,soc=null){
  const art=s("g",{transform:"translate("+x+" "+y+") scale("+scale+")",class:"rv7-equipment art-"+classes[id],"aria-hidden":"true"});
  const shape=(tag,attrs)=>art.appendChild(s(tag,attrs));
  const path=(d,cls)=>shape("path",{d,class:cls});
  const rect=(x,y,width,height,rx,cls)=>shape("rect",{x,y,width,height,rx,class:cls});
  if(id.startsWith("pv")){
   path("M3 30 34 30 45 41 14 41Z","art-ground");
   path("M22 24v11m-8 0h17","art-frame");
   path("M9 3 36 3 45 28 0 28Z","art-solar-frame");
   path("M11 6 34 6 41 25 4 25Z","art-solar");
   path("M17 6 14 25 M25 6 26 25 M6 15h32 M8 20h31","art-solar-cells");
   shape("circle",{cx:43,cy:5,r:4,class:"art-sun"});
  }else if(id.startsWith("inv")){
   path("M10 5 34 5 40 9 40 33 34 39 10 39Z","art-ground");
   rect(7,3,28,35,5,"art-device");
   rect(11,8,20,11,3,"art-display");
   path("M14 14q3-5 6 0t8 0","art-signal");
   path("M15 26h12m-12 4h12m-12 4h12","art-detail");
   shape("circle",{cx:21,cy:22,r:1.8,class:"art-led"});
   path("M13 38v4m8-4v4m8-4v4","art-frame");
  }else if(id==="load"){
   path("M2 34 37 34 45 40 9 40Z","art-ground");
   path("M6 17 23 5 39 17v19H6Z","art-house");
   path("M2 18 23 2 43 18 39 21 23 9 6 21Z","art-roof");
   rect(23,24,9,12,1,"art-door");
   rect(11,21,9,9,1,"art-window");
   path("M15.5 21v9m-4.5-4.5h9","art-frame");
  }else if(id==="battery"){
   path("M7 36h30l6 5H3Z","art-ground");
   rect(9,4,25,35,5,"art-device");
   rect(13,0,17,5,2,"art-terminal");
   rect(13,9,17,25,3,"art-battery-track");
   if(Number.isFinite(soc)){
    const fill=Math.max(0,Math.min(100,soc))*.21;
    rect(15,32-fill,13,fill,1,"art-battery-fill");
   }
   path("M23 11l-5 9h5l-3 8 8-11h-5Z","art-lightning");
  }else if(id==="grid"){
   path("M2 39h40l4 3H0Z","art-ground");
   path("M22 2 9 39M22 2 35 39M15 20h14M11 31h22M16 18l15 13m-18 0 15-13M5 10h34M0 20h44M5 10v5m34-5v5M0 20v5m44-5v5","art-tower");
  }
  group.appendChild(art);
 }
 function modal(parent,snap,id,trigger,scope){
  if(scope?.dialogClose)scope.dialogClose();
  const previous=parent.querySelector(".rv7-panel-backdrop");
  const prevPanel=parent.querySelector(".rv7-panel");
  if(previous)previous.remove();if(prevPanel)prevPanel.remove();
  const backdrop=h("div","rv7-panel-backdrop"),sheet=h("section","rv7-panel");
  sheet.setAttribute("role","dialog");sheet.setAttribute("aria-modal","true");
  sheet.setAttribute("aria-label","Деталі вузла");
  const title=h("div","rv7-panel-header"),close=h("button","rv7-panel-close","×");
  close.type="button";close.setAttribute("aria-label","Закрити деталі");
  put(title,h("h2","",labels[id]),close);sheet.appendChild(title);
  const reading=source(snap,id);
  sheet.appendChild(h("div","rv7-panel-big",id==="bus"?"Логічна енергетична шина":id.startsWith("inv")?labels[id]:M.fmt(reading)));
  if(id!=="bus"&&!id.startsWith("inv")){
   const data=[
    ["Джерело",reading?.slot_no?"Deye №"+reading.slot_no:"Не підтверджено"],
    ["Дата запису",M.ukTime(reading?.observed_at_utc)],
    ["Достовірність",reading?.quality||"missing"],
    ["Адреса регістра",reading?.register_address??"Не визначено"],
    ["Підтвердження регістра",reading?.register_verified?"Так":"Немає"]
   ];
   const key=id==="battery"?"battery_power":id==="load"?"load_power":
    id==="grid"?"grid_power":null;
   for(const [name,value] of data){
    const el=h("div","rv7-panel-item");
    put(el,h("small","",name),h("strong","",value));sheet.appendChild(el);
   }
   if(key){
    const group=h("div","rv7-panel-item");
    group.appendChild(h("small","","Покази двох Deye (не сума)"));
    for(const row of snap.reports[key].reports)
     group.appendChild(h("div","", "Deye №"+row.slot_no+": "+M.fmt(row)));
    sheet.appendChild(group);
   }
  }else if(id.startsWith("inv")){
   const slot=Number(id.slice(-1)),dev=snap.sources[slot-1].device;
   sheet.appendChild(h("p","rv7-panel-meta",dev?"Окремий звіт інвертора · "+M.ukTime(snap.sources[slot-1].reading.observed_at_utc):"Джерело телеметрії відсутнє"));
   for(const key of metrics){const row=h("div","rv7-panel-item");put(row,h("small","",key),h("strong","",M.fmt(M.read(dev?.values,key,dev))));sheet.appendChild(row);}
  }else sheet.appendChild(h("p","rv7-panel-meta",
   "Два PV окремо. АКБ, мережа й навантаження — фізично спільні. Неперевірені звіти не сумуються."));
  const link=h("a","rv7-link","Інвертори");link.href="#/parallel";
  const actions=h("div","rv7-panel-actions");actions.appendChild(link);sheet.appendChild(actions);
  const dismiss=()=>{
   backdrop.remove();sheet.remove();document.removeEventListener("keydown",keyHandler);
   if(scope?.dialogClose===dismiss)scope.dialogClose=null;
   if(trigger?.isConnected&&trigger.focus)trigger.focus();
  };
  if(scope)scope.dialogClose=dismiss;
  function keyHandler(e){
   if(e.key==="Escape"){e.preventDefault();dismiss();}
   if(e.key==="Tab"){
    if(e.shiftKey&&document.activeElement===close){e.preventDefault();link.focus();}
    if(!e.shiftKey&&document.activeElement===link){e.preventDefault();close.focus();}
   }
  }
  backdrop.addEventListener("click",dismiss);close.addEventListener("click",dismiss);
  document.addEventListener("keydown",keyHandler);
  put(parent,backdrop,sheet);close.focus();
 }
 function diagram(snap,onDetails,mobile){
  const g=geometry(mobile);
  const svg=s("svg",{viewBox:"0 0 "+g.w+" "+g.h,
   class:"rv7-diagram "+(mobile?"rv7-mobile":"rv7-desktop"),
   role:"group","aria-label":"Енергосистема: два PV, два Deye, одна АКБ, будинок і мережа"});
  for(const [id,path] of g.paths){
   const reading=id==="battery"?snap.reports.battery_power.reading:source(snap,id);
   const supported=reading?.valid && reading.number!==0 && (reading.quality!=="conflicting"||id.startsWith("pv"));
   const wire=s("path",{d:path,"data-flow":id,
    class:"rv7-wire "+(supported?"rv7-wire-"+id:"is-unknown")});
   svg.appendChild(wire);
   // Only verified PV production has an unambiguous directional meaning here.
   // Shared power reports are not confirmed system totals and must not imply direction.
   if((id==="pv1"||id==="pv2")&&supported&&reading.number>0&&reading.register_verified&&reading.quality==="live"){
    const p=wire.getPointAtLength(wire.getTotalLength()*.6);
    const q=wire.getPointAtLength(wire.getTotalLength()*.6+1);
    const angle=Math.atan2(q.y-p.y,q.x-p.x)*180/Math.PI;
    svg.appendChild(s("path",{d:"M-4 -4 L2 0 -4 4",transform:"translate("+p.x+" "+p.y+") rotate("+angle+")",class:"rv7-direction"}));
   }
  }
  for(const id of ["pv1","pv2","inv1","inv2","bus","load","battery","grid"]){
   const [x,y,w,height]=g.boxes[id],reading=source(snap,id),isInv=id.startsWith("inv"),isBus=id==="bus";
   const group=s("g",{"data-node":id,role:"button",tabindex:"0",
    "aria-label":labels[id]+(reading?": "+M.fmt(reading):"")});
   group.appendChild(s("rect",{x,y,width:w,height,rx:10,ry:10,class:"rv7-node-bg rv7-node-"+classes[id]}));
   if(isBus){
    group.appendChild(s("rect",{x:x+4,y:y+2,width:w-8,height:height-4,rx:12,class:"rv7-hub"}));
    group.appendChild(s("text",{x:x+w/2,y:y+height/2+4,"text-anchor":"middle",class:"rv7-bus-title"},"Шина"));
   }else if(isInv){
    const dev=snap.sources[Number(id.slice(-1))-1].device;
    const cx=x+w/2;
    if(mobile){
     equipment(group,id,x+10,y+8,1.03);
     group.appendChild(s("text",{x:x+56,y:y+25,class:"rv7-bus-title"},labels[id]));
     group.appendChild(s("text",{x:x+56,y:y+39,class:"rv7-bus-meta"},!dev?"Немає даних":dev.telemetry_error?"Помилка API":"Інвертор"));
    }else{
     equipment(group,id,cx-25,y+3,1.1);
     group.appendChild(s("text",{x:cx,y:y+66,"text-anchor":"middle",class:"rv7-bus-title"},labels[id]));
     group.appendChild(s("text",{x:cx,y:y+80,"text-anchor":"middle",class:"rv7-bus-meta"},!dev?"Немає даних":dev.telemetry_error?"Помилка API":"Інвертор"));
    }
   }else{
    const cx=x+w/2,title=id==="battery"?"Акумулятор":id==="pv1"?"Сонце 1":id==="pv2"?"Сонце 2":labels[id];
    const right=!mobile&&(id==="load"||id==="battery"||id==="grid");
    const size=mobile?.87:1.25;
    equipment(group,id,right?x+12:cx-23*size,y+3,size,id==="battery"?reading?.number:null);
    const tx=right?x+77:cx,anchor=right?"start":"middle";
    const labelY=y+(right?27:mobile?49:66),valueY=labelY+(mobile?19:25);
    group.appendChild(s("text",{x:tx,y:labelY,"text-anchor":anchor,class:"rv7-node-label"},title));
    group.appendChild(s("text",{x:tx,y:valueY,"text-anchor":anchor,class:"rv7-node-value",style:"font-size:"+(mobile?17:25)+"px"},M.fmt(reading)));
    if(id==="battery")group.appendChild(s("text",{x:tx,y:valueY+13,"text-anchor":anchor,class:"rv7-bus-meta"},M.fmt(snap.reports.battery_power.reading)));
   }
   const activate=()=>onDetails(id,group);
   group.addEventListener("click",activate);
   group.addEventListener("keydown",e=>{
    if(e.key==="Enter"||e.key===" "){e.preventDefault();activate();}
   });
   svg.appendChild(group);
  }
  return svg;
 }
 function engineering(container,snap){
  const zone=h("div","rv7-engineer");
  const head=h("div","rv7-card rv7-engineer-head");
  put(head,h("h2","","Інженерний режим"),
   h("p","","Джерела, відмінності показників і регістри · тільки читання"));
  zone.appendChild(head);
  const card=h("section","rv7-card"),box=h("div","rv7-engineer-table");
  const table=h("table"),tr=h("tr");
  for(const text of ["Показник","Deye №1","Deye №2","Статус"])
   tr.appendChild(h("th","",text));
  const thead=h("thead");thead.appendChild(tr);table.appendChild(thead);
  const tbody=h("tbody");
  for(const [key,name] of [["pv_power","PV"],["load_power","Будинок"],
   ["battery_power","Потужність АКБ"],["battery_soc","SOC"],["grid_power","Мережа"]]){
   const row=h("tr");row.appendChild(h("td","",name));
   const readings=snap.devices.map(dev=>M.read(dev.values,key,dev));
   for(const slot of [1,2]){
    const sample=readings.find(item=>Number(item.slot_no)===slot);
    row.appendChild(h("td","",sample?M.fmt(sample):"—"));
   }
   row.appendChild(h("td","",key==="pv_power"?"Окремі PV":
    snap.reports[key].conflict?"Відмінні звіти":"Не системний підсумок"));
   tbody.appendChild(row);
  }
  table.appendChild(tbody);box.appendChild(table);card.appendChild(box);
  const nav=h("nav","rv7-engineer-nav");
  for(const [route,text] of [["parallel","Інвертори"],["registers","Регістри V105.3"],
   ["modbus","Modbus"],["history","Історія"],["events","Події"]]){
   const link=h("a","rv7-link",text);link.href="#/"+route;nav.appendChild(link);
  }
  card.appendChild(nav);zone.appendChild(card);
  zone.appendChild(h("div","rv7-message",
   "◷ До фізичної перевірки Modbus адреси та масштаби регістрів залишаються непідтвердженими."));
  container.appendChild(zone);
 }
 function paintHistory(container,snap,config,scope,onFrame,onRestore){
  const card=h("section","rv7-card rv7-chart-card"),heading=h("div","rv7-card-head");
  heading.appendChild(h("strong","rv7-card-title","Динаміка потужності"));
  const ranges=h("div","rv7-ranges");
  heading.appendChild(ranges);card.appendChild(heading);
  const chartbox=h("div","rv7-chart-box"),legend=h("div","rv7-series");
  const time=h("div","rv7-history-time"),control=h("div","rv7-time-control");
  card.appendChild(chartbox);card.appendChild(legend);
  card.appendChild(time);card.appendChild(control);
  const historyNote=h("p","rv7-note","Дані MySQL. Натисніть на графік або перемістіть повзунок часу.");
  card.appendChild(historyNote);
  container.appendChild(card);
  let series=[],revision=0,explicitSelection=false,timer=null,availableTimes=[];
  let failedHistoryRequests=0;
  const historyError=h("p","rv7-history-error");
  historyError.setAttribute("role","status");
  card.insertBefore(historyError,time);
  const play=h("button","rv7-play","▶ Відтворити архів");
  play.type="button";
  play.setAttribute("aria-pressed","false");
  function stopPlayback(){
   if(timer!==null){clearInterval(timer);timer=null;}
   play.setAttribute("aria-pressed","false");
   play.textContent="▶ Відтворити архів";
   card.classList.remove("is-playing");
  }
  scope.onDispose=stopPlayback;
  play.addEventListener("click",()=>{
   if(timer!==null){stopPlayback();return;}
   if(availableTimes.length<2)return;
   play.setAttribute("aria-pressed","true");
   play.textContent="⏸ Пауза";
   card.classList.add("is-playing");
   timer=setInterval(()=>{
    if(scope.cancelled){stopPlayback();return;}
    const i=availableTimes.indexOf(chosenTime);
    chosenTime=availableTimes[(i+1)%availableTimes.length];
    explicitSelection=true;
    chart();
   },1200);
  });
  const metadata=[
   ["pv1","PV 1","pv_power",1,"var(--v7-pv)"],
   ["pv2","PV 2","pv_power",2,"var(--v7-primary)"],
   ["load","Будинок","load_power",0,"var(--v7-load)"],
   ["battery","АКБ","battery_power",0,"var(--v7-battery)"],
   ["grid","Мережа","grid_power",0,"var(--v7-grid)"],
   ["soc","SOC","battery_soc",0,"var(--v7-battery)"]
  ];
  function chart(){
   const present=series.filter(line=>views.has(line.id)&&line.points.length);
   const timestamps=Array.from(new Set(present.flatMap(line=>
    line.points.map(p=>p.timestamp)))).sort();
   const layer=s("svg",{viewBox:"0 0 480 180",class:"rv7-chart",
    role:"img","aria-label":"Архівний графік"});
   for(const y of [20,60,100,140])layer.appendChild(s("line",{
    x1:8,y1:y,x2:473,y2:y,class:"rv7-chart-gridline"}));
   availableTimes=timestamps;
   if(!timestamps.length){
    stopPlayback();
    chartbox.replaceChildren(h("div","rv7-chart-status",
     failedHistoryRequests?"Архів тимчасово недоступний — помилка API":
       "За цей період немає архівних вимірювань"));
    control.replaceChildren();time.textContent="";return;
   }
   const nums=present.filter(line=>line.id!=="soc").flatMap(line=>line.points.map(p=>p.number));
   const lo=Math.min(0,...nums),hi=Math.max(0,...nums),delta=Math.max(1,hi-lo);
   const first=Date.parse(timestamps[0]),last=Date.parse(timestamps.at(-1));
   const x=t=>42+(Date.parse(t)-first)/Math.max(1,last-first)*398;
   const y=n=>151-(n-lo)/delta*139;
   const socY=n=>151-n/100*139;
   for(const frac of [0,.5,1]){
    const val=lo+delta*frac;
    layer.appendChild(s("text",{x:35,y:y(val)+3,"text-anchor":"end",class:"rv7-axis"},M.pretty(val,1)));
    if(present.some(line=>line.id==="soc"))layer.appendChild(s("text",{x:446,y:socY(frac*100)+3,class:"rv7-axis"},Math.round(frac*100)+"%"));
   }
   layer.appendChild(s("text",{x:5,y:10,class:"rv7-axis"},"кВт"));
   for(const frac of [0,.33,.67,1]){
    const stamp=new Date(first+(last-first)*frac);
    const label=hours>24?stamp.toLocaleDateString("uk-UA",{timeZone:"Europe/Kyiv",day:"2-digit",month:"2-digit"}):stamp.toLocaleTimeString("uk-UA",{timeZone:"Europe/Kyiv",hour:"2-digit",minute:"2-digit"});
    layer.appendChild(s("text",{x:42+398*frac,y:174,"text-anchor":frac===0?"start":frac===1?"end":"middle",class:"rv7-axis"},label));
   }
   for(const line of present){
    let d="";
    const intervals=line.points.slice(1).map((p,i)=>Date.parse(p.timestamp)-Date.parse(line.points[i].timestamp)).filter(n=>n>0).sort((a,b)=>a-b);
    const cadence=intervals.length?intervals[Math.floor((intervals.length-1)/2)]:0;
    const gapLimit=Math.max(1200000,3*cadence);
    for(const [i,p] of line.points.entries()){
     const gap=i&&Date.parse(p.timestamp)-Date.parse(line.points[i-1].timestamp)>gapLimit;
     d+=(i&&!gap?" L":" M")+x(p.timestamp).toFixed(1)+" "+(line.id==="soc"?socY(p.number):y(p.number)).toFixed(1);
    }
    layer.appendChild(s("path",{d,stroke:line.color,class:"rv7-chart-line"}));
    if(line.points.length===1){const p=line.points[0];layer.appendChild(s("circle",{cx:x(p.timestamp),cy:line.id==="soc"?socY(p.number):y(p.number),r:3,fill:line.color}));}
   }
   if(!chosenTime||!timestamps.includes(chosenTime))chosenTime=timestamps.at(-1);
   const cursor=x(chosenTime);
   layer.appendChild(s("line",{x1:cursor,y1:9,x2:cursor,y2:154,
    class:"rv7-chart-cursor"}));
   const active=present.map(line=>{
    const v=M.historicalValue(line.points,chosenTime);
    const stat=h("div","rv7-chart-stat");
    const label=h("small","",line.label);label.style.color=line.color;
    put(stat,label,h("strong","",v?M.pretty(v.number)+" "+(v.unit||"кВт"):"—"));
    return stat;
   });
   time.replaceChildren(h("div","rv7-chart-date",M.ukTime(chosenTime)),put(h("div","rv7-chart-stats"),...active));
   if(explicitSelection&&typeof onFrame==="function")onFrame(chosenTime,series);
   function select(index){
    explicitSelection=true;
    chosenTime=timestamps[Math.max(0,Math.min(timestamps.length-1,index))];
    chart();
   }
   const hit=s("rect",{x:0,y:0,width:480,height:180,class:"rv7-chart-hit"});
   hit.addEventListener("click",e=>{
    const matrix=layer.getScreenCTM();
    if(!matrix)return;
    const point=layer.createSVGPoint();point.x=e.clientX;point.y=e.clientY;
    const local=point.matrixTransform(matrix.inverse());
    const target=first+Math.max(0,Math.min(1,(local.x-42)/398))*(last-first);
    let best=0;for(let i=1;i<timestamps.length;i++)if(Math.abs(Date.parse(timestamps[i])-target)<Math.abs(Date.parse(timestamps[best])-target))best=i;
    select(best);
   });
   layer.appendChild(hit);chartbox.replaceChildren(layer);
   const slider=h("input");slider.type="range";
   slider.min="0";slider.max=String(timestamps.length-1);
   slider.value=String(timestamps.indexOf(chosenTime));
   slider.setAttribute("aria-label","Час архівного запису");
   slider.addEventListener("input",()=>select(Number(slider.value)));
   const row=h("label");
   put(row,h("span","","Час вимірювання"),
    h("span","",timestamps.length+" записів"));
   const restore=h("button","rv7-latest","Останній запис");restore.type="button";
   restore.addEventListener("click",()=>{stopPlayback();explicitSelection=false;chosenTime=timestamps.at(-1);onRestore?.();chart();});
   control.replaceChildren(row,slider,put(h("div","rv7-playback-actions"),play,restore));
  }
  async function fetchSeries(){
   const current=++revision;
   stopPlayback();
   chartbox.replaceChildren(h("div","rv7-chart-status","Завантаження архіву…"));
   failedHistoryRequests=0;historyError.textContent="";
   legend.replaceChildren();time.textContent="";control.replaceChildren();
   if(typeof config.historyLoader!=="function"){
    chartbox.replaceChildren(h("div","rv7-chart-status",
     "Історія доступна після підключення до архіву MySQL"));return;
   }
   const planned=metadata.map(([id,label,key,slot,color])=>({
    id,label,key,color,device:slot?snap.devices.find(x=>Number(x.slot_no)===slot):snap.selected
   }));
   const got=await Promise.allSettled(planned.map(async item=>{
    if(!item.device?.device_key)return [];
    const r=await config.historyLoader(item.device.device_key,item.key,hours);
    return M.normalizeHistory(r?.points||[]);
   }));
   if(scope.cancelled||current!==revision)return;
   failedHistoryRequests=got.filter(item=>item.status==="rejected").length;
   historyError.textContent=failedHistoryRequests ?
    "Не вдалося прочитати частину архіву ("+failedHistoryRequests+"). Повторіть запит.":"";
   series=planned.map((item,i)=>({...item,
    points:got[i].status==="fulfilled"?got[i].value:[]}));
   for(const line of series){
    const button=h("button","rv7-series-button");button.type="button";
    button.setAttribute("aria-pressed",String(views.has(line.id)));
    const circle=h("span","rv7-series-dot");
    circle.style.setProperty("--series-color",line.color);
    put(button,circle,h("span","",line.label));
    button.addEventListener("click",()=>{
     if(views.has(line.id))views.delete(line.id);else views.add(line.id);
     button.setAttribute("aria-pressed",String(views.has(line.id)));
     chart();
    });
    legend.appendChild(button);
   }
   chart();
  }
  function drawRanges(){
   ranges.replaceChildren();
   for(const [value,label] of [[24,"24 год"],[72,"3 дні"],[168,"7 днів"]]){
    const button=h("button","rv7-range",label);
    button.type="button";button.setAttribute("aria-pressed",String(hours===value));
    button.addEventListener("click",()=>{
     hours=value;chosenTime=null;explicitSelection=false;
     if(typeof onRestore==="function")onRestore();
     drawRanges();fetchSeries();
    });
    ranges.appendChild(button);
   }
  }
  drawRanges();fetchSeries();
 }
 function render(host,config={}){
  if(!M)throw Error("RakursV7Model not loaded");
  const last=handlers.get(host);
  if(last)disposeScope(last);
  const scope={cancelled:false,dialogClose:null};handlers.set(host,scope);
  activeScopes.add(scope);
  const snap=M.snapshot(config),app=h("div","rv7");
  app.setAttribute("aria-label","Енергосистема Rakurs V7");
  const top=h("header","rv7-top"),heading=h("div","rv7-heading");
  const copy=h("div");
  put(copy,h("h2","rv7-title","Енергосистема"),
   h("span","rv7-subtitle","Два паралельні інвертори"));
  put(heading,copy);
  const statuses=h("div","rv7-status");
  statuses.appendChild(h("span","rv7-pill is-"+snap.data_state,
   snap.data_state==="archive"?"◷ Архів":
    snap.data_state==="live"?"● Онлайн":"○ Немає даних"));
  if(snap.updated_at_utc)statuses.appendChild(
   h("span","rv7-pill",M.ukTime(snap.updated_at_utc)));
  put(top,heading,statuses);app.appendChild(top);
  const tabs=h("div","rv7-tabs");tabs.setAttribute("role","tablist");
  for(const [value,label] of [["overview","Огляд"],["engineer","Інженерний режим"]]){
   const button=h("button","rv7-tab",label);button.type="button";
   button.setAttribute("role","tab");
   button.setAttribute("aria-selected",String(mode===value));
   button.addEventListener("click",()=>{mode=value;render(host,config);});
   tabs.appendChild(button);
  }
  app.appendChild(tabs);
  const layout=h("div","rv7-layout");app.appendChild(layout);
  if(mode==="engineer")engineering(layout,snap);
  else{
   const shell=h("div","rv7-shell"),leftColumn=h("div","rv7-overview-left");
   const diagramCard=h("section","rv7-card rv7-diagram-card");
   const dh=h("div","rv7-card-head");
   put(dh,h("strong","rv7-card-title","Енергопотоки"),
    h("span","rv7-card-meta",snap.data_state==="live"?"Поточний стан":"Збережений стан"));
   diagramCard.appendChild(dh);
   const wrapped=h("div","rv7-schema-wrap");
   let displayed=snap;
   const show=(id,trigger)=>modal(app,displayed,id,trigger,scope);
   function paint(){
    wrapped.replaceChildren(diagram(displayed,show,false),diagram(displayed,show,true));
   }
   paint();
   diagramCard.appendChild(wrapped);
   const foot=h("div","rv7-flow-foot");
   foot.appendChild(h("span","rv7-source-label",
    snap.selected?.slot_no?"Спільні вузли: звіт Deye №"+snap.selected.slot_no+" · не сума":"Джерело не встановлено"));
   const actions=h("div","rv7-actions");
   const detailButton=h("button","rv7-link","Деталі");detailButton.type="button";
   detailButton.addEventListener("click",()=>{mode="engineer";render(host,config);});
   actions.appendChild(detailButton);foot.appendChild(actions);
   diagramCard.appendChild(foot);
   leftColumn.appendChild(diagramCard);
   shell.appendChild(leftColumn);
   paintHistory(shell,snap,config,scope,(timestamp,series)=>{
    // Historical frames are built only from near-time measurements.
    // Missing records stay missing; do not silently reuse current values.
    const deviceRows=(snap.devices||[]).map(d=>{
      const archive={};
      for(const row of series.filter(s=>s.device?.device_key===d.device_key)){
        const p=M.historicalValue(row.points,timestamp,600000);
        if(p)archive[row.key]={value_decimal:p.number,
          unit:p.unit||M.read(d.values,row.key,d).unit,
          observed_at_utc:p.timestamp,quality:p.quality,sign_verified:false,register_verified:false};
      }
      return {...d,values:archive};
    });
    displayed=M.snapshot({...config,devices:deviceRows,
      fallbackValues:{},status:{},now:Date.parse(timestamp)});
    paint();
    diagramCard.querySelector(".rv7-card-meta").textContent=
      "Архів · "+M.ukTime(timestamp);
   },()=>{
    displayed=snap;paint();
    diagramCard.querySelector(".rv7-card-meta").textContent=
      snap.data_state==="live"?"Поточний стан":"Збережений стан";
   });
   layout.appendChild(shell);
   if(snap.warnings.length){
    const notices=h("section","rv7-warnings");
    const item=h("div","rv7-message");
    const count=snap.warnings.length;
    const summary=h("span","",count===1?
      "Є одна невизначеність у збережених показниках":
      "Є "+count+" уточнення щодо архівних показників");
    const open=h("button","rv7-link","Перевірити");
    open.type="button";
    open.addEventListener("click",()=>{mode="engineer";render(host,config);});
    put(item,h("span","rv7-notice-icon","ⓘ"),summary,open);
    notices.appendChild(item);
     const details=h("details","rv7-warning-details");
     details.appendChild(h("summary","","Що саме потребує перевірки"));
     const reasons=h("ul");
     for(const reason of snap.warnings)reasons.appendChild(h("li","",reason));
     details.appendChild(reasons);notices.appendChild(details);
     leftColumn.appendChild(notices);
   }
   const more=h("section","rv7-secondary");
   const devices=h("div","rv7-card");
   devices.appendChild(h("h3","rv7-secondary-title","Два інвертори"));
   const two=h("div","rv7-devices");
   for(const item of snap.sources){
    const card=h("div","rv7-device");
    put(card,h("strong","","Deye №"+item.slot),
     h("small","",item.device?"Збережені показники":"Немає джерела"),
     h("a","rv7-device-link","Показники →"));
    card.querySelector("a").href="#/parallel";
    two.appendChild(card);
   }
   devices.appendChild(two);more.appendChild(devices);
   const shortcuts=h("div","rv7-card");
   shortcuts.appendChild(h("h3","rv7-secondary-title","Розділи"));
   const nav=h("nav","rv7-chip-grid");
   for(const [route,label] of [
    ["trends","⌁ Графіки"],["history","◷ Історія"],
    ["events","◇ Події"],["registers","▥ Регістри"]]){
    const a=h("a","rv7-chip",label);a.href="#/"+route;nav.appendChild(a);
   }
   shortcuts.appendChild(nav);more.appendChild(shortcuts);
   layout.appendChild(more);
  }
  host.replaceChildren(app);
  return Object.freeze({snapshot:snap,root:app,dispose:()=>disposeScope(scope)});
 }
 function enabled(){
  return true;
 }
 root.RakursV7=Object.freeze({render,enabled,geometry,diagram,
   disposeAll:()=>{for(const scope of Array.from(activeScopes))disposeScope(scope);},
  reset:()=>{mode="overview";hours=24;chosenTime=null;}});
})(window);
