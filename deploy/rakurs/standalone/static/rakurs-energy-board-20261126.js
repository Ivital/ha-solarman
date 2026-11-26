"use strict";
/* Rakurs shared-bus energy flow from archived MySQL readings.
 * Physical topology supplied by operator: only PV1 and PV2 are separate;
 * battery, load and grid are each ONE common physical node.
 * Independent inverter reports may refer to the same node, or be inverter-local
 * contributions. Without register semantics verified, never add them and never
 * present one selected report as an authoritative system total.
 * Flow animation represents an ILLUSTRATION of a saved historical snapshot,
 * not real-time measurement. Read-only module, no device writes. */
(function (root) {
  const SLOT_KEYS = ["pv_power","load_power","grid_power","battery_power","battery_soc"];
  let motionEnabled=true;
  let motionSpeed="normal";
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
    const board=make(doc,"section","energy-board"+(s.confirmed?" is-live":" is-archive")+
      (motionEnabled?" is-animated":" is-paused")+" speed-"+motionSpeed);
    board.setAttribute("aria-label","Схема напрямків енергії системи Deye");
    const head=make(doc,"div","energy-board-header");
    const heading=make(doc,"div","energy-board-heading");
    append(heading,make(doc,"span","energy-board-kicker","ЕНЕРГІЯ НА ОДНОМУ ЕКРАНІ"),
      make(doc,"h2","","Як рухається енергія"),
      make(doc,"p","","Натисніть на елемент або лінію потоку, щоб переглянути його показники."));
    const flags=make(doc,"div","energy-board-flags");
    append(flags,make(doc,"span","energy-board-flag "+(s.confirmed?"is-online":"is-stale"),
      s.confirmed?"● Підтверджене опитування":"◷ Архівний зріз — не онлайн"),
      make(doc,"span","energy-board-date","Запис: "+ukTime(s.last)+" · "+ageLabel(s.last)));
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
    const toolbar=make(doc,"div","energy-board-toolbar");
    const motion=make(doc,"button","energy-board-motion","");
    motion.type="button";
    const speed=make(doc,"button","energy-board-speed","");
    speed.type="button";
    const caption=make(doc,"span","energy-board-visual-label",
      s.confirmed?"Схема за підтвердженими даними":"Схематичний рух за архівним записом");
    function paintMotion(){
      board.className=board.className.replace(/\s+is-animated|\s+is-paused|\s+speed-(normal|slow|fast)/g,"")+
        (motionEnabled?" is-animated":" is-paused")+" speed-"+motionSpeed;
      motion.textContent=motionEnabled?"⏸ Пауза":"▶ Відтворити";
      motion.setAttribute("aria-pressed",String(motionEnabled));
      motion.setAttribute("aria-label",motionEnabled?"Зупинити рух на схемі":"Відтворити схематичний рух");
      speed.textContent=motionSpeed==="slow"?"Швидкість: повільно":
        motionSpeed==="fast"?"Швидкість: швидко":"Швидкість: звичайна";
      speed.setAttribute("aria-label","Змінити швидкість анімації потоків");
    }
    motion.addEventListener("click",()=>{motionEnabled=!motionEnabled;paintMotion();});
    speed.addEventListener("click",()=>{
      motionSpeed=motionSpeed==="normal"?"fast":motionSpeed==="fast"?"slow":"normal";
      paintMotion();
    });
    paintMotion();
    append(toolbar,caption,motion,speed);
    board.appendChild(toolbar);
    const context=make(doc,"div","energy-board-context",s.focus==="system"?
      "Два масиви сонячних панелей, два Deye, один будинок, одна АКБ та одна мережа.":
      "Показники з архіву Deye №"+(s.selected?.slot_no||"—")+
        ". Будинок, акумулятор і мережа залишаються одними вузлами.");
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
      house:"Навантаження будинку. Число взяте з архіву вибраного Deye і ще не підтверджене як загальний баланс.",
      battery:"Заряд або розряд акумулятора. SOC не сумується; знак потужності уточнимо за реальними регістрами.",
      grid:"Імпорт чи експорт електроенергії. Напрямок і одиниці виміру треба звірити з регістрами обладнання."
    };
    function node(kind,key,icon,label,num,detail,subtitle,diagnostics=[]){
      const el=make(doc,"button","energy-board-node energy-board-"+kind,"");
      el.type="button";
      el.setAttribute("aria-label",label+": показ архіву "+pretty(num,"кВт"));
      append(el,make(doc,"span","energy-board-node-icon",icon),
        make(doc,"span","energy-board-node-name",label),
        make(doc,"strong","energy-board-node-value",pretty(num,"кВт")),
        make(doc,"span","energy-board-node-detail",detail));
      if(subtitle)el.appendChild(make(doc,"small","energy-board-node-note",subtitle));
      const diagnosticText=diagnostics.map(row=>
        "Deye №"+row.slot+": "+pretty(row.value,"кВт")+
        " ("+row.state.caption+")"+
        (row.soc!==null?" · SOC "+pretty(row.soc,"%"):"")+
        ", запис "+ukTime(row.last)).join("; ");
      interactive.push({el,key,label,num,
        detail:(desc[key]||"")+" "+(diagnosticText?
          "Покази обох інверторів: "+diagnosticText+".":"Немає окремих звітів."),
        subtitle});
      return el;
    }
    function wire(key,direction,active,caption){
      const item=make(doc,"button","energy-board-wire "+(active?"has-flow":"no-flow")+
        (direction==="←"?" is-reverse":" is-forward")+" wire-"+key);
      item.type="button";
      item.setAttribute("aria-label",caption+" — "+(active?"напрямок "+direction:"немає підтвердженого потоку"));
      item.setAttribute("aria-pressed","false");
      item.title=caption+" · Натисніть для деталей";
      const line=make(doc,"span","energy-board-flow-track");
      line.setAttribute("aria-hidden","true");
      if(active){
        line.appendChild(make(doc,"span","energy-board-flow-head"));
        for(let i=0;i<3;i++){
          const particle=make(doc,"span","energy-board-flow-particle");
          particle.setAttribute("aria-hidden","true");
          line.appendChild(particle);
        }
      }
      append(item,make(doc,"span","energy-board-wire-title",caption),line,
        make(doc,"span","energy-board-direction",active?direction:"—"));
      wires[key]=item;return item;
    }
    // A shared load, grid, or battery has exactly one physical connection.
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
      append(details,make(doc,"small","","Сонце · Deye №"+number),
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
    const systemMode=s.focus==="system";
    const slots=[1,2].map(slot=>({slot,device:s.devices.find(d=>Number(d.slot_no)===slot)||null}));
    function diagnosticRows(info){
      return slots.map(({slot,device})=>{
        const value=device?metric(device.values,info.signal):null;
        return {slot,value,state:flowState(info.key,value),
          soc:info.key==="battery"&&device?metric(device.values,"battery_soc"):null,
          last:device?measured(device.values):null};
      });
    }
    const outputs=[
      {key:"house",signal:"load_power",icon:"⌂",label:"Будинок"},
      {key:"battery",signal:"battery_power",icon:"▣",label:"Акумулятор"},
      {key:"grid",signal:"grid_power",icon:"⌁",label:"Мережа"}
    ];
    for(const info of outputs){
      const rows=diagnosticRows(info);
      const selectedRow=rows.find(row=>row.slot===Number(s.selected?.slot_no)&&row.value!==null);
      const alternate=systemMode?rows.find(row=>row.value!==null):null;
      const reference=selectedRow||alternate||null;
      const reported=value(info.signal);
      const metricValue=reported!==null?reported:reference?.value??null;
      const referenceSlot=reported!==null?s.selected?.slot_no:reference?.slot;
      const present=rows.filter(row=>row.value!==null);
      const incompatible=systemMode&&present.length===2 &&
        present[0].state.direction!==present[1].state.direction;
      const diverged=present.length===2&&present[0].value!==present[1].value;
      const flow=incompatible?
        {direction:"—",caption:"Напрямок невідомий",active:false}:
        flowState(info.key,metricValue);
      const subtitle=(referenceSlot?"Архів Deye №"+referenceSlot:"Архів без джерела")+
        (diverged?" · покази Deye різні":"");
      // Single line to the single physical node. Never create two arrows.
      sinkWires.appendChild(wire(info.key,flow.direction,flow.active,flow.caption));
      const stateNote=info.key==="battery"?
        " · SOC "+pretty(reported!==null?
          metric(s.values,"battery_soc"):reference?.soc??null,"%"):"";
      sinks.appendChild(node(info.key,info.key,info.icon,info.label,
        metricValue,flow.caption+stateNote,subtitle,rows));
    }
    append(map,sources,sourceWires,hub,sinkWires,sinks);board.appendChild(map);
    const legend=make(doc,"div","energy-board-legend");
    append(legend,
      make(doc,"span","energy-board-legend-key","☀ Генерація"),
      make(doc,"span","energy-board-legend-key","⌂ Споживання"),
      make(doc,"span","energy-board-legend-key","▣ Акумулятор"),
      make(doc,"span","energy-board-legend-key","⌁ Мережа"));
    board.appendChild(legend);
    const foot=make(doc,"div","energy-board-foot");
    const inspector=make(doc,"div","energy-board-inspector");
    inspector.setAttribute("aria-live","polite");
    append(inspector,make(doc,"strong","energy-board-inspector-title","Натисніть на елемент"),
      make(doc,"p","energy-board-inspector-description",
        "Сонце, будинок, акумулятор, мережа та лінії енергії відкривають пояснення і записи Deye."));
    const notice=make(doc,"p","energy-board-disclaimer",
      "Анімація ілюструє останній збережений зріз, а не рух енергії зараз. " +
      "Показники взято з обраного Deye, їх не додаємо без звірки регістрів.");
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
        for(const key of Object.keys(wires)){
          wires[key].setAttribute("aria-pressed",String(key===item.key));
        }
        inspector.replaceChildren();
        append(inspector,
          make(doc,"strong","energy-board-inspector-title",item.title||item.label+" · "+pretty(item.num,"кВт")),
          make(doc,"p","energy-board-inspector-description",item.detail),
          make(doc,"small","energy-board-inspector-timestamp",
            (item.subtitle?item.subtitle+" · ":"")+"Стан: "+(s.confirmed?"підтверджене опитування":"архівний запис")));
      }
    }
    for(const item of interactive){
      item.el.addEventListener("click",()=>choose(item));
      if(wires[item.key])wires[item.key].addEventListener("click",()=>choose(item));
    }
    host.appendChild(board);
    return {board,mode:s.focus,confirmed:s.confirmed,devices:s.devices.length};
  }
  root.RakursEnergyBoard=Object.freeze({render,snapshot,metric,flowState});
})(window);
