"use strict";
/* Rakurs energy flow: visual read-only view of archival MySQL readings.
 * Each Deye is independent. Shared battery/SOC/grid/load are NEVER aggregated.
 * Motion requires confirmed physical collection and measurements <=120 s old.
 * No fetch, serial I/O, writable request or persistent telemetry state. */
(function (root) {
  const SLOT_KEYS = ["pv_power","load_power","grid_power","battery_power","battery_soc"];
  const ukTime = value => {
    if (!value) return "час невідомий";
    const d = new Date(value);
    return Number.isFinite(d.getTime()) ?
      d.toLocaleString("uk-UA",{timeZone:"Europe/Kyiv",day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"}) :
      "час невідомий";
  };
  function metric(values,key) {
    const row=values?.[key];
    if(!row || row.value_decimal===null || row.value_decimal===undefined || row.value_decimal==="")return null;
    const n=Number(row.value_decimal);
    return Number.isFinite(n)?n:null;
  }
  function measured(values) {
    const seen=SLOT_KEYS.map(key=>values?.[key]?.observed_at_utc)
      .filter(value=>value&&Number.isFinite(new Date(value).getTime()));
    return seen.length ? new Date(Math.max(...seen.map(value=>new Date(value).getTime()))).toISOString() : null;
  }
  function pretty(value,unit="") {
    return value===null?"—":value.toLocaleString("uk-UA",{maximumFractionDigits:3})+(unit?" "+unit:"");
  }
  function make(doc,tag,className,content){
    const el=doc.createElement(tag);
    if(className)el.className=className;
    if(content!==undefined)el.textContent=String(content);
    return el;
  }
  function append(parent,...nodes){for(const node of nodes)parent.appendChild(node);return parent;}
  function snapshot(config){
    const devices=(config.devices||[]).slice().sort((a,b)=>Number(a.slot_no||0)-Number(b.slot_no||0));
    const selected=devices.find(d=>d.device_key===config.selectedDeviceKey)||
      devices.find(d=>Number(d.slot_no)===Number(config.selectedSlotNo))||devices[0]||null;
    const values=selected?.values && Object.keys(selected.values).length?
      selected.values:config.fallbackValues||{};
    const focus=String(config.mode||"system");
    // Do not invent a device when the MySQL parallel-device API is unavailable.
    const sourceDevices=devices;
    const wanted=focus==="system"?selected:
      sourceDevices.find(d=>String(d.slot_no)===focus)||selected;
    const outputValues=wanted?.values && Object.keys(wanted.values).length?
      wanted.values:focus==="system"||wanted?.device_key===config.selectedDeviceKey?
      config.fallbackValues||{}:{};
    const measuredTimes=focus==="system" && sourceDevices.length ?
      sourceDevices.map(device=>measured(device.values)).filter(Boolean) :
      [measured(outputValues)].filter(Boolean);
    const last=measuredTimes.length ?
      new Date(Math.max(...measuredTimes.map(value=>new Date(value).getTime()))).toISOString():null;
    const inWindow=value=>{
      if(!value)return false;
      const age=Date.now()-new Date(value).getTime();
      return age>=-60000 && age<=120000;
    };
    const fresh=focus==="system"?
      sourceDevices.length===2 && sourceDevices.every(device=>inWindow(measured(device.values))) :
      inWindow(last);
    // All three gates must be explicitly verified; archival data are never animated.
    const confirmed=Boolean(config.status?.collector_active&&config.status?.physical_topology_verified&&fresh);
    return {devices:sourceDevices,selected:wanted,values:outputValues,last,
      fresh,confirmed,focus,loading:config.loading===true};
  }
  function flowState(key,value) {
    if(value===null||value===undefined)return {direction:"—",caption:"Немає даних",active:false};
    if(value===0)return {direction:"—",caption:"Без обміну",active:false};
    if(key==="battery")return value>0?
      {direction:"←",caption:"Розряд",active:true}:
      {direction:"→",caption:"Заряд",active:true};
    if(key==="grid")return value>0?
      {direction:"←",caption:"Імпорт",active:true}:
      {direction:"→",caption:"Експорт",active:true};
    if(key==="house")return value>0?
      {direction:"→",caption:"До навантаження",active:true}:
      {direction:"—",caption:"Перевірте знак",active:false};
    return value>0?
      {direction:"→",caption:"Генерація",active:true}:
      {direction:"—",caption:"Перевірте знак",active:false};
  }
  function ageLabel(timestamp) {
    if(!timestamp)return "давність невідома";
    const minutes=Math.floor((Date.now()-new Date(timestamp).getTime())/60000);
    if(!Number.isFinite(minutes)||minutes<0)return "час потребує перевірки";
    if(minutes<2)return "запис щойно";
    if(minutes<60)return minutes+" хв тому";
    if(minutes<1440)return Math.floor(minutes/60)+" год "+minutes%60+" хв тому";
    return Math.floor(minutes/1440)+" дн "+Math.floor(minutes%1440/60)+" год тому";
  }
  function render(host,config={}) {
    if(!host || !host.replaceChildren) return;
    const doc=config.document||document;
    const s=snapshot(config);
    host.replaceChildren();
    const board=make(doc,"section","energy-board"+(s.confirmed?" is-live":" is-archive"));
    board.setAttribute("aria-label","Схема напрямків енергії системи Deye");
    const head=make(doc,"div","energy-board-header");
    const heading=make(doc,"div","energy-board-heading");
    append(heading,make(doc,"span","energy-board-kicker","НА ОДНОМУ ЕКРАНІ"),
      make(doc,"h2","","Енергетична схема системи"),
      make(doc,"p","","Напрямки за останніми збереженими вимірюваннями. Натисніть на будь-який вузол, щоб дізнатися більше."));
    const flags=make(doc,"div","energy-board-flags");
    append(flags,make(doc,"span","energy-board-flag "+(s.confirmed?"is-online":"is-stale"),
      s.confirmed?"● Підтверджене опитування":"◷ Архівний зріз — не онлайн"),
      make(doc,"span","energy-board-date","Найновіший запис: "+ukTime(s.last)+" · "+ageLabel(s.last)));
    append(head,heading,flags);board.appendChild(head);

    const switches=make(doc,"div","energy-board-modes");
    switches.setAttribute("role","group");
    switches.setAttribute("aria-label","Обрати схему енергосистеми");
    const modes=[["system","Уся система"],...s.devices.map((d,i)=>
      [String(d.slot_no||i+1),"Deye №"+(d.slot_no||i+1)])];
    const unique=new Set();
    for(const [key,label] of modes){
      if(unique.has(key))continue;
      unique.add(key);
      const button=make(doc,"button","energy-board-mode"+(s.focus===key?" active":""),label);
      button.type="button";button.setAttribute("aria-pressed",String(s.focus===key));
      button.addEventListener("click",()=>{
        if(s.focus===key)return;
        if(typeof config.onMode==="function")config.onMode(key);
      });
      switches.appendChild(button);
    }
    board.appendChild(switches);
    const context=make(doc,"div","energy-board-context",s.focus==="system"?
      "Повна система: ліворуч генерація кожного Deye, праворуч ОКРЕМІ покази №1 та №2 для спільних вузлів. Значення не підсумовуються.":
      "Детальний архівний зріз Deye №"+(s.selected?.slot_no||"—")+
      ". Покази спільної батареї, будинку та мережі належать обраному інвертору.");
    board.appendChild(context);

    const map=make(doc,"div","energy-board-map");
    const sources=make(doc,"div","energy-board-sources");
    const sourceWires=make(doc,"div","energy-board-wires source-wires");
    const hub=make(doc,"div","energy-board-hub");
    const sinkWires=make(doc,"div","energy-board-wires sink-wires");
    const sinks=make(doc,"div","energy-board-sinks");
    const interactive=[];
    const wires={};
    const value=key=>metric(s.values,key);
    const desc={
      solar1:"Фотоелектричні панелі відповідного Deye. Жодної суми без підтвердженої схеми.",
      solar2:"Показник фотоелектричних панелей другого Deye — окреме джерело.",
      house:"Навантаження з обраного інвертора, не підтверджена загальна потужність двох.",
      battery:"Спільна батарея. Знак потужності трактуємо як розряд (+) або заряд (−) за профілем; SOC двох Deye не додаємо.",
      grid:"Додатне значення — імпорт із мережі; від’ємне — експорт. Нуль — відсутність обміну."
    };
    function node(kind,key,icon,label,num,detail,subtitle,rows=null){
      const comparing=Array.isArray(rows)&&rows.length>0;
      const el=make(doc,"button","energy-board-node energy-board-"+kind+
        (comparing?" is-comparison":""),"");
      el.type="button";
      el.setAttribute("aria-label",comparing?
        label+": окремі показники Deye №1 та Deye №2, без підсумовування":
        label+": "+pretty(num,"кВт"));
      if(comparing){
        const title=make(doc,"span","energy-board-node-top");
        append(title,make(doc,"span","energy-board-node-icon",icon),
          make(doc,"strong","energy-board-node-name",label),
          make(doc,"span","energy-board-node-unit","без суми"));
        el.appendChild(title);
        const details=make(doc,"span","energy-board-comparison");
        for(const row of rows){
          const cell=make(doc,"span","energy-board-reading-row"+(row.value===null?" is-missing":""));
          append(cell,make(doc,"span","energy-board-reading-label","Deye №"+row.slot),
            make(doc,"strong","energy-board-reading-value",pretty(row.value,"кВт")),
            make(doc,"small","energy-board-reading-state",
              row.state.caption+(row.soc!==null?" · SOC "+pretty(row.soc,"%"):"")));
          details.appendChild(cell);
        }
        el.appendChild(details);
      }else{
        append(el,make(doc,"span","energy-board-node-icon",icon),
          make(doc,"span","energy-board-node-name",label),
          make(doc,"strong","energy-board-node-value",pretty(num,"кВт")),
          make(doc,"span","energy-board-node-detail",detail));
        if(subtitle)el.appendChild(make(doc,"small","energy-board-node-note",subtitle));
      }
      const detailMessage=comparing?
        rows.map(row=>"Deye №"+row.slot+": "+pretty(row.value,"кВт")+
          " ("+row.state.caption+")"+
          (row.soc!==null?" · SOC "+pretty(row.soc,"%"):"")+
          " · "+ukTime(row.last)).join("; "):
        detail;
      interactive.push({el,key,label,num:comparing?null:num,
        title:comparing?label+" · 2 незалежні покази":null,
        detail:comparing?detailMessage+"; спільні показники не сумуються.":detail,
        subtitle:comparing?null:subtitle});
      return el;
    }
    function wire(key,direction,active,caption){
      const item=make(doc,"div","energy-board-wire "+(active?"has-flow":"no-flow")+" wire-"+key);
      const arrow=make(doc,"span","energy-board-direction",active?direction:"—");
      append(item,make(doc,"span","energy-board-wire-title",caption),arrow);
      wires[key]=item;return item;
    }
    function comparisonWire(key,rows){
      const active=rows.some(row=>row.state.active);
      const item=make(doc,"div","energy-board-wire is-comparison-wire "+
        (active?"has-flow":"no-flow")+" wire-"+key);
      for(const row of rows){
        const lane=make(doc,"span","energy-board-wire-lane "+
          (row.state.active?"has-flow":"no-flow"));
        lane.setAttribute("title","Deye №"+row.slot+": "+row.state.caption);
        append(lane,make(doc,"span","energy-board-wire-number","№"+row.slot),
          make(doc,"strong","energy-board-wire-arrow",
            row.state.active?row.state.direction:"—"));
        item.appendChild(lane);
      }
      wires[key]=item;
      return item;
    }
    const source1=s.devices.find(d=>Number(d.slot_no)===1)||null;
    const source2=s.devices.find(d=>Number(d.slot_no)===2)||null;
    for(const [number,device] of [[1,source1],[2,source2]]){
      const power=device?metric(device.values,"pv_power"):null;
      const exists=Boolean(device);
      const modeOn=s.focus==="system"||s.focus===String(number);
      const choice=make(doc,"button","energy-board-source"+(modeOn?" is-included":" is-dimmed"),"");
      choice.type="button";
      choice.setAttribute("aria-label","Фотоелектричні панелі Deye №"+number+": "+pretty(power,"кВт"));
      const icon=make(doc,"span","energy-board-source-icon","☀");
      const details=make(doc,"div","energy-board-source-text");
      append(details,make(doc,"small","","Панелі → Deye №"+number),
        make(doc,"strong","",exists?pretty(power,"кВт"):"—"),
        make(doc,"span","",exists?"Запис: "+ukTime(measured(device.values)):
          s.loading?"Отримуємо дані з MySQL":"Немає даних цього інвертора"));
      append(choice,icon,details);
      const key="solar"+number;
      interactive.push({el:choice,key,label:"Генерація Deye №"+number,
        num:power,detail:desc[key]||desc.solar1,
        subtitle:exists?"Останній запис: "+ukTime(measured(device.values)):"Дані недоступні"});
      choice.addEventListener("dblclick",()=>{
        if(typeof config.onMode==="function")config.onMode(String(number));
      });
      sources.appendChild(choice);
      sourceWires.appendChild(wire(key,"→",modeOn&&power!==null&&power>0,"PV "+number));
    }
    append(hub,make(doc,"span","energy-board-hub-icon","⚡"),
      make(doc,"strong","","ЕНЕРГЕТИЧНА ШИНА"),
      make(doc,"small","",s.focus==="system"?"Deye №1 + Deye №2":"Deye №"+s.selected?.slot_no),
      make(doc,"span","energy-board-hub-note",""));
    const hubNote=hub.childNodes[hub.childNodes.length-1];
    hubNote.textContent="Зв’язки показано за збереженим зрізом";
    hub.setAttribute("role","img");
    hub.setAttribute("aria-label","Спільна схема двох Deye, без розрахунку непідтвердженої суми");
    const load=value("load_power"),battery=value("battery_power"),soc=value("battery_soc"),grid=value("grid_power");
    const systemMode=s.focus==="system"&&s.devices.length>0;
    const slots=[1,2].map(slot=>({slot,device:s.devices.find(d=>Number(d.slot_no)===slot)||null}));
    function comparisonRows(info){
      return slots.map(({slot,device})=>{
        const v=device?metric(device.values,info.signal):null;
        return {slot,value:v,state:flowState(info.key,v),
          soc:info.key==="battery"&&device?metric(device.values,"battery_soc"):null,
          last:device?measured(device.values):null};
      });
    }
    const sourceLabel="Дані "+(s.selected?"Deye №"+s.selected.slot_no:"запису MySQL");
    const outputs=[
      {key:"house",signal:"load_power",icon:"⌂",label:"Будинок",v:load,extra:sourceLabel},
      {key:"battery",signal:"battery_power",icon:"▣",label:"Спільна АКБ",v:battery,
        extra:soc===null?"SOC —":("SOC "+pretty(soc,"%")+" · "+sourceLabel)},
      {key:"grid",signal:"grid_power",icon:"⌁",label:"Електромережа",v:grid,extra:sourceLabel}
    ];
    for(const info of outputs){
      const state=flowState(info.key,info.v);
      const rows=systemMode?comparisonRows(info):null;
      sinkWires.appendChild(systemMode?
        comparisonWire(info.key,rows):
        wire(info.key,state.direction,state.active,state.caption));
      sinks.appendChild(node(info.key,info.key,info.icon,info.label,
        info.v,state.caption,info.extra,rows));
    }
    append(map,sources,sourceWires,hub,sinkWires,sinks);board.appendChild(map);
    const legend=make(doc,"div","energy-board-legend");
    append(legend,
      make(doc,"span","energy-board-legend-key","→ напрям до вузла"),
      make(doc,"span","energy-board-legend-key","← напрям від вузла"),
      make(doc,"span","energy-board-legend-key","— немає потоку / даних"));
    board.appendChild(legend);
    const foot=make(doc,"div","energy-board-foot");
    const inspector=make(doc,"div","energy-board-inspector");
    inspector.setAttribute("aria-live","polite");
    append(inspector,make(doc,"strong","energy-board-inspector-title","Як читати схему"),
      make(doc,"p","energy-board-inspector-description",
        "Натисніть на сонячні панелі, будинок, батарею або мережу — відповідний потік підсвітиться."));
    const notice=make(doc,"p","energy-board-disclaimer",
      "Це збережені вимірювання MySQL, не онлайн-телеметрія. Стрілки відображають напрямок за знаком показника; " +
      "спільні SOC, навантаження та мережа не сумуються. Анімація можлива лише за підтвердженого живого опитування.");
    append(foot,inspector,notice);board.appendChild(foot);
    function choose(item){
      for(const candidate of interactive){
        candidate.el.className=candidate.el.className.replace(/\s+is-focused/g,"");
        candidate.el.setAttribute("aria-pressed",String(candidate===item));
      }
      if(item){
        item.el.className+=" is-focused";
        for(const key of Object.keys(wires))wires[key].className=wires[key].className.replace(/\s+is-highlight/g,"");
        if(wires[item.key])wires[item.key].className+=" is-highlight";
        inspector.replaceChildren();
        append(inspector,
          make(doc,"strong","energy-board-inspector-title",item.title||item.label+" · "+pretty(item.num,"кВт")),
          make(doc,"p","energy-board-inspector-description",item.detail),
          make(doc,"small","energy-board-inspector-timestamp",
            (item.subtitle?item.subtitle+" · ":"")+"Стан: "+(s.confirmed?"підтверджене опитування":"архівний запис")));
      }
    }
    for(const item of interactive)item.el.addEventListener("click",()=>choose(item));
    host.appendChild(board);
    return {board,mode:s.focus,confirmed:s.confirmed,devices:s.devices.length};
  }
  root.RakursEnergyBoard=Object.freeze({render,snapshot,metric,flowState});
})(window);
