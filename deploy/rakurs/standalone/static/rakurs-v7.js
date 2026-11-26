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
  if(mobile)return {w:360,h:286,
   boxes:{pv1:[20,4,152,62],pv2:[188,4,152,62],inv1:[30,88,132,46],inv2:[198,88,132,46],
    bus:[132,156,96,26],load:[4,211,112,70],battery:[124,211,112,70],grid:[244,211,112,70]},
   paths:[["pv1","M96 66 V88"],["pv2","M264 66 V88"],
    ["inv1","M96 134 V145 H156 V156"],["inv2","M264 134 V145 H204 V156"],
    ["bus","M180 182 V196 M60 196 H300"],["load","M60 196 V211"],
    ["battery","M180 196 V211"],["grid","M300 196 V211"]]};
  return {w:760,h:286,
   boxes:{pv1:[12,24,142,78],pv2:[12,184,142,78],inv1:[204,32,140,62],inv2:[204,192,140,62],
    bus:[393,116,100,54],load:[552,8,196,78],battery:[552,104,196,78],grid:[552,200,196,78]},
   paths:[["pv1","M154 63 H204"],["pv2","M154 223 H204"],
    ["inv1","M344 63 H368 V132 H393"],["inv2","M344 223 H368 V154 H393"],
    ["bus","M493 143 H524 M524 47 V239"],["load","M524 47 H552"],
    ["battery","M524 143 H552"],["grid","M524 239 H552"]]};
 }
 function icon(group,id,x,y){
  const paths={
   pv1:"M1 3h18l3 13H-2L1 3m5 0-1 13m8-13 1 13M0 9h20M7 16v4m-4 0h8",
   pv2:"M1 3h18l3 13H-2L1 3m5 0-1 13m8-13 1 13M0 9h20M7 16v4m-4 0h8",
   inv1:"M3 1h14v19H3zM7 5h6v4H7zM6 15q2-4 4 0t4 0",
   inv2:"M3 1h14v19H3zM7 5h6v4H7zM6 15q2-4 4 0t4 0",
   load:"M0 10 10 1l10 9M3 8v12h14V8m-10 12v-7h6v7",
   battery:"M1 5h18v12H1zM19 9h3v4h-3M5 11h4m-2-2v4m6-2h3",
   grid:"M10 1 2 21m8-20 8 20M4 15h12M6 9h8M1 5h18M1 5v4m18-4v4",
   bus:"M1 10h18M5 5v10m10-10v10"};
  group.appendChild(s("path",{d:paths[id],transform:"translate("+x+" "+y+")",class:"rv7-equipment-icon"}));
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
    ["Походження",reading?.reported_quality==="generated"?"Згенеровано для тестування":reading?.reported_quality||"Невідоме"],
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
    group.appendChild(s("text",{x:x+w/2,y:y+height/2+4,"text-anchor":"middle",class:"rv7-bus-title"},"Енерг. шина"));
   }else if(isInv){
    icon(group,id,x+12,y+12);
    const dev=snap.sources[Number(id.slice(-1))-1].device;
    group.appendChild(s("text",{x:x+40,y:y+19,class:"rv7-bus-title"},labels[id]));
    group.appendChild(s("text",{x:x+40,y:y+33,class:"rv7-bus-meta"},!dev?"Немає даних":dev.telemetry_error?"Помилка API":"Інвертор"));
   }else{
    const title=id==="battery"?"АКБ":id==="pv1"?"PV 1":id==="pv2"?"PV 2":labels[id];
    icon(group,id,x+10,y+9);
    group.appendChild(s("text",{x:x+36,y:y+20,class:"rv7-node-label"},title));
    group.appendChild(s("text",{x:x+10,y:y+43,class:"rv7-node-value",style:"font-size:"+(w<130?15:20)+"px"},M.fmt(reading)));
    const detail=id==="battery"?M.fmt(snap.reports.battery_power.reading):id.startsWith("pv")?"Deye №"+id.slice(-1):"Звіт Deye "+(reading?.slot_no||"—");
    group.appendChild(s("text",{x:x+10,y:y+height-7,class:"rv7-bus-meta"},detail));
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
  heading.appendChild(h("strong","rv7-card-title","Історія"));
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
   for(const t of [timestamps[0],timestamps.at(-1)])layer.appendChild(s("text",{x:x(t),y:174,"text-anchor":t===timestamps[0]?"start":"end",class:"rv7-axis"},new Date(t).toLocaleTimeString("uk-UA",{timeZone:"Europe/Kyiv",hour:"2-digit",minute:"2-digit"})));
   for(const line of present){
    let d="";
    for(const [i,p] of line.points.entries()){
     const gap=i&&Date.parse(p.timestamp)-Date.parse(line.points[i-1].timestamp)>1200000;
     d+=(i&&!gap?" L":" M")+x(p.timestamp).toFixed(1)+" "+(line.id==="soc"?socY(p.number):y(p.number)).toFixed(1);
    }
    layer.appendChild(s("path",{d,stroke:line.color,class:"rv7-chart-line"}));
   }
   if(!chosenTime||!timestamps.includes(chosenTime))chosenTime=timestamps.at(-1);
   const cursor=x(chosenTime);
   layer.appendChild(s("line",{x1:cursor,y1:9,x2:cursor,y2:154,
    class:"rv7-chart-cursor"}));
   const active=present.map(line=>{
    const v=M.historicalValue(line.points,chosenTime);
    return line.label+": "+(v?M.pretty(v.number)+" "+(v.unit||"кВт"):"—");
   });
   time.textContent=M.ukTime(chosenTime)+"  ·  "+active.join("  |  ");
   if(explicitSelection&&typeof onFrame==="function")onFrame(chosenTime,series);
   function select(index){
    explicitSelection=true;
    chosenTime=timestamps[Math.max(0,Math.min(timestamps.length-1,index))];
    chart();
   }
   const hit=s("rect",{x:0,y:0,width:480,height:180,class:"rv7-chart-hit"});
   hit.addEventListener("click",e=>{
    const rect=layer.getBoundingClientRect(),ratio=
     Math.max(0,Math.min(1,(e.clientX-rect.left)/Math.max(1,rect.width)));
    select(Math.round(ratio*(timestamps.length-1)));
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
   control.replaceChildren(row,slider,play);
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
   const synthetic=series.some(line=>line.points.some(p=>["generated","simulated","demo","synthetic"].includes(p.quality)));
   historyNote.textContent=synthetic?"Містить тестові згенеровані записи. Не є фактичними вимірюваннями.":"Дані MySQL. Натисніть на графік або перемістіть повзунок часу.";
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
   h("span","rv7-subtitle","2 × Deye · диспетчеризація"));
  put(heading,copy);
  const statuses=h("div","rv7-status");
  statuses.appendChild(h("span","rv7-pill is-"+snap.data_state,
   snap.generated?"◷ Архів · тестові дані":snap.data_state==="archive"?"◷ Архів":
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
   put(dh,h("strong","rv7-card-title","Потоки енергії"),
    h("span","rv7-card-meta",snap.generated?"Демонстраційний стан":snap.data_state==="live"?"Поточний стан":"Збережений стан"));
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
      (displayed.generated?"Тестовий архів · ":"Архів · ")+M.ukTime(timestamp);
   },()=>{
    displayed=snap;paint();
    diagramCard.querySelector(".rv7-card-meta").textContent=
      snap.generated?"Демонстраційний стан":snap.data_state==="live"?"Поточний стан":"Збережений стан";
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
     h("span","",M.fmt(item.reading)));
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
