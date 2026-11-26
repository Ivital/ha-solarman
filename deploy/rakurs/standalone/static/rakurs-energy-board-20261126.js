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
    const sourceDevices=devices.length?devices:[{
      device_key:config.selectedDeviceKey||"unverified",
      slot_no:config.selectedSlotNo||1,values:config.fallbackValues||{},
      model:"Deye"
    }];
    const wanted=focus==="system"?selected:
      sourceDevices.find(d=>String(d.slot_no)===focus)||selected;
    const outputValues=wanted?.values && Object.keys(wanted.values).length?
      wanted.values:focus==="system"||wanted?.device_key===config.selectedDeviceKey?
      config.fallbackValues||{}:{};
    const last=measured(outputValues);
    const fresh=last!==null&&Date.now()-new Date(last).getTime()>=-60000&&
      Date.now()-new Date(last).getTime()<=120000;
    const confirmed=Boolean(config.status?.collector_active&&config.status?.physical_topology_verified&&fresh);
    return {devices:sourceDevices,selected:wanted,values:outputValues,last,
      fresh,confirmed,focus,loading:config.loading===true};
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
      make(doc,"span","energy-board-date","Запис: "+ukTime(s.last)));
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
      "Два інвертори окремо. Праворуч — значення "+(s.selected?"Deye №"+s.selected.slot_no:"обраного інвертора")+
        "; показники спільних вузлів не сумуються.":
      "Детальний зріз Deye №"+s.selected?.slot_no+
        ". Спільна батарея, навантаження й мережа відображаються за записами цього інвертора.");
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
    function node(kind,key,icon,label,num,detail,subtitle){
      const el=make(doc,"button","energy-board-node energy-board-"+kind,"");
      el.type="button";el.setAttribute("aria-label",label+": "+pretty(num,kind==="battery"?"кВт":"кВт"));
      append(el,make(doc,"span","energy-board-node-icon",icon),
        make(doc,"span","energy-board-node-name",label),
        make(doc,"strong","energy-board-node-value",pretty(num,"кВт")),
        make(doc,"span","energy-board-node-detail",detail));
      if(subtitle)el.appendChild(make(doc,"small","energy-board-node-note",subtitle));
      interactive.push({el,key,label,num,detail,subtitle});
      return el;
    }
    function wire(key,direction,active,caption){
      const item=make(doc,"div","energy-board-wire "+(active?"has-flow":"no-flow")+" wire-"+key);
      const arrow=make(doc,"span","energy-board-direction",active?direction:"—");
      append(item,make(doc,"span","energy-board-wire-title",caption),arrow);
      wires[key]=item;return item;
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
    const outputs=[
      {key:"house",icon:"⌂",label:"Будинок",v:load,dir:"→",
        active:load!==null&&load>0,caption:load===null?"Немає даних":load===0?"Без споживання":"До навантаження",
        extra:"Дані Deye №"+(s.selected?.slot_no||1)},
      {key:"battery",icon:"▣",label:"Спільна АКБ",v:battery,
        dir:battery===null||battery===0?"—":battery>0?"←":"→",
        active:battery!==null&&battery!==0,caption:battery===null?"Немає даних":battery===0?"Без обміну":battery>0?"Розряд":"Заряд",
        extra:soc===null?"SOC —":("SOC "+pretty(soc,"%")+" · дані Deye №"+(s.selected?.slot_no||1))},
      {key:"grid",icon:"⌁",label:"Електромережа",v:grid,
        dir:grid===null||grid===0?"—":grid>0?"←":"→",
        active:grid!==null&&grid!==0,caption:grid===null?"Немає даних":grid===0?"Без обміну":grid>0?"Імпорт":"Експорт",
        extra:"Дані Deye №"+(s.selected?.slot_no||1)}
    ];
    for(const info of outputs){
      sinkWires.appendChild(wire(info.key,info.dir,info.active,info.caption));
      sinks.appendChild(node(info.key,info.key,info.icon,info.label,
        info.v,info.caption,info.extra));
    }
    append(map,sources,sourceWires,hub,sinkWires,sinks);board.appendChild(map);
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
          make(doc,"strong","energy-board-inspector-title",item.label+" · "+pretty(item.num,"кВт")),
          make(doc,"p","energy-board-inspector-description",item.detail),
          make(doc,"small","energy-board-inspector-timestamp",
            (item.subtitle?item.subtitle+" · ":"")+"Стан: "+(s.confirmed?"підтверджене опитування":"архівний запис")));
      }
    }
    for(const item of interactive)item.el.addEventListener("click",()=>choose(item));
    host.appendChild(board);
    return {board,mode:s.focus,confirmed:s.confirmed,devices:s.devices.length};
  }
  root.RakursEnergyBoard=Object.freeze({render,snapshot,metric});
})(window);
