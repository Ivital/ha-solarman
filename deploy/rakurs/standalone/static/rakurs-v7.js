"use strict";
/* Rakurs V7: data-driven SVG dispatch, read-only archive & engineering mode.
   No telemetry is fabricated and V6 remains the default interface. */
(function(root){
 const M=root.RakursV7Model,NS="http://www.w3.org/2000/svg";
 let mode="overview",hours=24,chosenTime=null;
 const views=new Set(["pv1","pv2","load"]);
 const handlers=new WeakMap();
 const classes={pv1:"pv",pv2:"pv",bus:"bus",load:"load",battery:"battery",grid:"grid"};
 const labels={pv1:"PV · Deye №1",pv2:"PV · Deye №2",bus:"Два інвертори",
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
  if(id==="load")return snap.reports.load_power.reading;
  if(id==="grid")return snap.reports.grid_power.reading;
  if(id==="battery")return snap.reports.battery_soc.reading;
  return null;
 }
 function geometry(mobile){
  if(mobile)return {
   w:360,h:330,
   boxes:{pv1:[9,10,165,76],pv2:[186,10,165,76],bus:[105,132,150,67],
    load:[7,236,108,81],battery:[126,236,108,81],grid:[245,236,108,81]},
   paths:[["pv1","M91 86 V106 Q91 117 104 121 L152 132"],
    ["pv2","M269 86 V106 Q269 117 256 121 L208 132"],
    ["bus","M180 199 V216 M62 216 H299"],
    ["load","M62 216 V236"],["battery","M180 216 V236"],["grid","M299 216 V236"]]
  };
  return {
   w:860,h:296,
   boxes:{pv1:[16,26,174,78],pv2:[223,26,174,78],bus:[427,104,170,84],
    load:[651,16,190,76],battery:[651,109,190,76],grid:[651,202,190,76]},
   paths:[["pv1","M190 65 H402 L427 128"],
    ["pv2","M397 65 H414 L427 160"],
    ["bus","M597 146 H621 M621 54 V240"],
    ["load","M621 54 H651"],["battery","M621 147 H651"],["grid","M621 240 H651"]]
  };
 }
 function modal(parent,snap,id,trigger){
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
  sheet.appendChild(h("div","rv7-panel-big",id==="bus"?"Deye №1 + №2":M.fmt(reading)));
  if(id!=="bus"){
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
  }else sheet.appendChild(h("p","rv7-panel-meta",
   "Два PV окремо. АКБ, мережа й навантаження — фізично спільні. Неперевірені звіти не сумуються."));
  const link=h("a","rv7-link","Інвертори");link.href="#/parallel";
  const actions=h("div","rv7-panel-actions");actions.appendChild(link);sheet.appendChild(actions);
  const dismiss=()=>{
   backdrop.remove();sheet.remove();document.removeEventListener("keydown",keyHandler);
   if(trigger?.isConnected&&trigger.focus)trigger.focus();
  };
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
   const reading=source(snap,id);
   svg.appendChild(s("path",{d:path,"data-flow":id,
    class:"rv7-wire "+(id==="bus"||reading?.valid?"rv7-wire-"+id:"is-unknown")}));
  }
  for(const id of ["pv1","pv2","bus","load","battery","grid"]){
   const [x,y,w,height]=g.boxes[id],small=w<130,reading=source(snap,id);
   const group=s("g",{"data-node":id,role:"button",tabindex:"0",
    "aria-label":labels[id]+(reading?": "+M.fmt(reading):"")});
   group.appendChild(s("rect",{x,y,width:w,height,rx:15,ry:15,
    class:"rv7-node-bg rv7-node-"+classes[id]}));
   if(id==="bus"){
    group.appendChild(s("circle",{cx:x+w/2,cy:y+25,r:15,
     fill:"var(--v7-card)",stroke:"var(--v7-line)"}));
    group.appendChild(s("text",{x:x+w/2,y:y+32,"text-anchor":"middle",
     class:"rv7-node-icon"},"ϟ"));
    group.appendChild(s("text",{x:x+w/2,y:y+49,"text-anchor":"middle",
     class:"rv7-bus-title"},"Інверторна шина"));
    group.appendChild(s("text",{x:x+w/2,y:y+61,"text-anchor":"middle",
     class:"rv7-bus-meta"},"Deye №1 + №2"));
   }else{
    const title=id==="battery"?"АКБ":labels[id];
    group.appendChild(s("text",{x:x+(small?9:14),y:y+(small?21:28),
     class:"rv7-node-label"},title));
    group.appendChild(s("text",{x:x+(small?9:14),y:y+(small?47:56),
     class:"rv7-node-value",style:"font-size:"+(small?14:19)+"px"},M.fmt(reading)));
    if(id==="battery")group.appendChild(s("text",{
     x:x+(small?9:14),y:y+height-9,class:"rv7-bus-meta"},
     M.fmt(snap.reports.battery_power.reading)));
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
 function paintHistory(container,snap,config,scope,onFrame){
  const card=h("section","rv7-card rv7-chart-card"),heading=h("div","rv7-card-head");
  heading.appendChild(h("strong","rv7-card-title","Історія"));
  const ranges=h("div","rv7-ranges");
  heading.appendChild(ranges);card.appendChild(heading);
  const chartbox=h("div","rv7-chart-box"),legend=h("div","rv7-series");
  const time=h("div","rv7-history-time"),control=h("div","rv7-time-control");
  card.appendChild(chartbox);card.appendChild(legend);
  card.appendChild(time);card.appendChild(control);
  card.appendChild(h("p","rv7-note",
   "Дані MySQL. Натисніть на графік або перемістіть повзунок часу."));
  container.appendChild(card);
  let series=[],revision=0,explicitSelection=false;
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
   if(!timestamps.length){
    chartbox.replaceChildren(h("div","rv7-chart-status",
     "За цей період немає архівних вимірювань"));
    control.replaceChildren();time.textContent="";return;
   }
   const nums=present.flatMap(line=>line.points.map(p=>p.number));
   const lo=Math.min(0,...nums),hi=Math.max(0,...nums),delta=Math.max(1,hi-lo);
   const first=Date.parse(timestamps[0]),last=Date.parse(timestamps.at(-1));
   const x=t=>12+(Date.parse(t)-first)/Math.max(1,last-first)*456;
   const y=n=>151-(n-lo)/delta*139;
   for(const line of present){
    let d="";
    for(const [i,p] of line.points.entries())d+=(i?" L":"M")+
     x(p.timestamp).toFixed(1)+" "+y(p.number).toFixed(1);
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
   control.replaceChildren(row,slider);
  }
  async function fetchSeries(){
   const current=++revision;
   chartbox.replaceChildren(h("div","rv7-chart-status","Завантаження архіву…"));
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
   series=planned.map((item,i)=>({...item,
    points:got[i].status==="fulfilled"?got[i].value:[]}));
   for(const line of series.filter(row=>row.id!=="soc")){
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
     hours=value;chosenTime=null;explicitSelection=false;drawRanges();fetchSeries();
    });
    ranges.appendChild(button);
   }
  }
  drawRanges();fetchSeries();
 }
 function render(host,config={}){
  if(!M)throw Error("RakursV7Model not loaded");
  const last=handlers.get(host);if(last)last.cancelled=true;
  const scope={cancelled:false};handlers.set(host,scope);
  const snap=M.snapshot(config),app=h("div","rv7");
  app.setAttribute("aria-label","Енергосистема Rakurs V7");
  const top=h("header","rv7-top"),heading=h("div","rv7-heading");
  const copy=h("div");
  put(copy,h("h2","rv7-title","Енергосистема"),
   h("span","rv7-subtitle","Два паралельні Deye"));
  put(heading,h("div","rv7-appmark","R"),copy);
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
   const shell=h("div","rv7-shell");
   const diagramCard=h("section","rv7-card rv7-diagram-card");
   const dh=h("div","rv7-card-head");
   put(dh,h("strong","rv7-card-title","Потоки енергії"),
    h("span","rv7-card-meta",snap.data_state==="live"?"Поточний стан":"Збережений стан"));
   diagramCard.appendChild(dh);
   const wrapped=h("div","rv7-schema-wrap");
   let displayed=snap;
   const show=(id,trigger)=>modal(app,displayed,id,trigger);
   function paint(){
    wrapped.replaceChildren(diagram(displayed,show,false),diagram(displayed,show,true));
   }
   paint();
   diagramCard.appendChild(wrapped);
   const foot=h("div","rv7-flow-foot");
   foot.appendChild(h("span","rv7-source-label",
    snap.selected?.slot_no?"Вузли: архів Deye №"+snap.selected.slot_no:"Джерело не встановлено"));
   const actions=h("div","rv7-actions");
   const detailButton=h("button","rv7-link","Деталі");detailButton.type="button";
   detailButton.addEventListener("click",()=>{mode="engineer";render(host,config);});
   actions.appendChild(detailButton);foot.appendChild(actions);
   diagramCard.appendChild(foot);
   shell.appendChild(diagramCard);
   paintHistory(shell,snap,config,scope,(timestamp,series)=>{
    // Historical frames are built only from near-time measurements.
    // Missing records stay missing; do not silently reuse current values.
    const deviceRows=(snap.devices||[]).map(d=>{
      const archive={};
      for(const row of series.filter(s=>s.device?.device_key===d.device_key)){
        const p=M.historicalValue(row.points,timestamp,600000);
        if(p)archive[row.key]={value_decimal:p.number,
          unit:p.unit||M.read(d.values,row.key,d).unit,
          observed_at_utc:p.timestamp,sign_verified:false,register_verified:false};
      }
      return {...d,values:archive};
    });
    displayed=M.snapshot({...config,devices:deviceRows,
      fallbackValues:{},status:{},now:Date.parse(timestamp)});
    paint();
    diagramCard.querySelector(".rv7-card-meta").textContent=
      "Архів · "+M.ukTime(timestamp);
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
    notices.appendChild(item);layout.appendChild(notices);
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
  return Object.freeze({snapshot:snap,root:app,dispose:()=>{scope.cancelled=true;}});
 }
 function enabled(){
  return new URLSearchParams(root.location.search).get("ux")==="v7";
 }
 root.RakursV7=Object.freeze({render,enabled,geometry,diagram,
  reset:()=>{mode="overview";hours=24;chosenTime=null;}});
})(window);
