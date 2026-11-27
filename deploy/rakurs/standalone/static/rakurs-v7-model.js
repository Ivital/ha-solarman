"use strict";
/* Rakurs V7 — pure, read-only quality and provenance adapter.
   Public model is deliberately independent of DOM and server availability. */
(function(root,factory){
  const model=factory();
  if(typeof module==="object"&&module.exports)module.exports=model;
  if(root)root.RakursV7Model=model;
})(typeof window!=="undefined"?window:undefined,function(){
  const POWER_KEYS=["pv_power","load_power","battery_power","grid_power"];
  const SHARED_KEYS=["load_power","battery_power","grid_power","battery_soc"];
  const LABELS={pv_power:"Генерація",load_power:"Будинок",
    battery_power:"Акумулятор",grid_power:"Мережа",battery_soc:"Рівень заряду"};
  const NUM=(v)=>v===null||v===undefined||v===""?null:
    Number.isFinite(Number(v))?Number(v):null;
  function date(input){
    if(!input)return null;
    const d=new Date(input);
    if(!Number.isFinite(d.getTime()))return null;
    const fraction=String(input).match(/\.(\d{1,6})(?:Z|[+-]\d{2}:\d{2})$/i);
    const micro=fraction?fraction[1].padEnd(6,"0").slice(3):"000";
    return d.toISOString().replace(/Z$/,micro+"Z");
  }
  function read(values,key,device){
    const item=values&&typeof values==="object"?values[key]:undefined;
    const number=NUM(item?.value_decimal);
    const measured_at_utc=date(item?.observed_at_utc);
    let unit=String(item?.unit||((key==="battery_soc")?"%":"кВт"));
    // Never convert watts implicitly; retain the reported unit and its scale.
    const registerAddress=item?.register_address??item?.modbus_address??null;
    const reported_quality=String(item?.quality||"unknown");
    return Object.freeze({
      key,number,unit,reported_quality,
      valid:number!==null,
      observed_at_utc:measured_at_utc,
      device_key:device?.device_key||null,
      slot_no:device?.slot_no||null,
      register_address:registerAddress,
      sign_verified:item?.sign_verified===true&&item?.register_verified===true,
      register_verified:item?.register_verified===true,
      scope:device?.device_key?"device_report":"unscoped"
    });
  }
  function lastOf(items){
    const dates=items.map(item=>date(item?.observed_at_utc)).filter(Boolean);
    return dates.length?dates.sort().at(-1):null;
  }
  function fresh(ts,now){
    if(!ts)return false;
    const age=now-Date.parse(ts);
    return age>=-60000&&age<=120000;
  }
  function pretty(number,decimals=2){
    if(number===null||!Number.isFinite(number))return "—";
    return number.toLocaleString("uk-UA",{maximumFractionDigits:decimals});
  }
  function fmt(item){
    return item&&item.valid?pretty(item.number)+(item.unit?" "+item.unit:""):"—";
  }
  function ukTime(value){
    if(!date(value))return "Час відсутній";
    return new Date(value).toLocaleString("uk-UA",{
      timeZone:"Europe/Kyiv",day:"2-digit",month:"2-digit",
      year:"numeric",hour:"2-digit",minute:"2-digit"
    });
  }
  function quality(item,ctx){
    if(!item?.valid)return "missing";
    if(!item.observed_at_utc)return "unverified";
    if(ctx.isLive&&item.register_verified&&fresh(item.observed_at_utc,ctx.now))return "live";
    return "archive";
  }
  function differing(a,b){
    if(!a?.valid||!b?.valid)return false;
    if(a.unit!==b.unit)return true;
    // This is a difference between independent device reports, not evidence of fault.
    const delta=Math.abs(a.number-b.number),scale=Math.max(Math.abs(a.number),Math.abs(b.number),1);
    return delta>Math.max(.15,.02*scale);
  }
  function snapshot(config={}){
    const now=Number.isFinite(config.now)?config.now:Date.now();
    const devices=(Array.isArray(config.devices)?config.devices:[])
      .filter(d=>d&&[1,2].includes(Number(d.slot_no)))
      .sort((a,b)=>Number(a.slot_no)-Number(b.slot_no));
    const bySlot=new Map(devices.map(d=>[Number(d.slot_no),d]));
    const selected=devices.find(d=>d.device_key===config.selectedDeviceKey)||
      bySlot.get(Number(config.selectedSlotNo))||devices[0]||null;
    const selectedValues=selected?.values||{};
    const values=selected?selectedValues:(config.fallbackValues||{});
    // Never fabricate a second device or place fallback readings into an unknown PV slot.
    const fallbackUnscoped=!selected&&Object.keys(config.fallbackValues||{}).length>0;
    const sources=[1,2].map(slot=>{
      const device=bySlot.get(slot);
      return {slot,device:device||null,reading:read(device?.values||{},"pv_power",device)};
    });
    const reports={};
    for(const key of SHARED_KEYS){
      const byDevice=devices.map(device=>read(device.values,key,device));
      const first=byDevice.find(item=>item.device_key===selected?.device_key);
      const primary=first?.valid?first:
        (read(values,key,selected).valid?read(values,key,selected):first||read(values,key,selected));
      reports[key]={
        key,reading:primary,
        reports:byDevice,conflict:byDevice.length>=2&&differing(byDevice[0],byDevice[1]),
        physical_node:"one",verified_system_total:false
      };
    }
    const validReadings=[
      ...sources.map(x=>x.reading),...SHARED_KEYS.map(key=>reports[key].reading)
    ].filter(item=>item.valid);
    const last=lastOf(validReadings);
    const generated=validReadings.some(item=>["generated","simulated","demo","synthetic"].includes(item.reported_quality));
    const reportedFresh=devices.length===2&&devices.every(d=>{
      return d.telemetry_error!==true&&["pv_power",...SHARED_KEYS].every(key=>{
        const item=read(d.values,key,d);
        return item.valid&&item.register_verified&&!["generated","simulated","demo","synthetic"].includes(item.reported_quality)&&fresh(item.observed_at_utc,now);
      });
    });
    // All three physical gates + firmware/register semantics must be verified.
    const isLive=Boolean(config.status?.collector_active===true&&
      config.status?.physical_topology_verified===true&&
      config.status?.register_semantics_verified===true&&reportedFresh&&
      !SHARED_KEYS.some(key=>reports[key].conflict));
    const ctx={isLive,now};
    for(const pv of sources)pv.reading={...pv.reading,quality:quality(pv.reading,ctx)};
    for(const key of SHARED_KEYS){
      reports[key].reading={...reports[key].reading,
        quality:reports[key].conflict?"conflicting":quality(reports[key].reading,ctx)};
    }
    // Batteries and grid are shared but reports may refer to inverter-local shares.
    // Keep sources visible; do not sum two reports and never assert system totals.
    const warnings=[];
    if(fallbackUnscoped)warnings.push("Немає підтвердженої прив'язки архіву до конкретного Deye");
    if(devices.length<2)warnings.push("Не всі інвертори зареєстровані в архіві");
    for(const device of devices)if(device.telemetry_error===true)
      warnings.push("Не вдалося прочитати покази Deye №"+device.slot_no+" з API");
    for(const key of SHARED_KEYS)if(reports[key].conflict)
      warnings.push("Покази вузла «"+LABELS[key]+"» від двох Deye відрізняються");
    const alignedPv=sources.every(x=>x.reading.valid&&x.reading.unit===sources[0].reading.unit&&
      x.reading.observed_at_utc&&sources[0].reading.observed_at_utc&&
      Math.abs(Date.parse(x.reading.observed_at_utc)-Date.parse(sources[0].reading.observed_at_utc))<=60000);
    // Even with aligned PV timestamps, a verified aggregate is still required.
    return Object.freeze({
      devices,selected,source:"MySQL",data_state:isLive?"live":validReadings.length?"archive":"missing",
      updated_at_utc:last,generated,show_aggregate_pv:false,pv_time_aligned:alignedPv,
      sources,reports,warnings,read_only:true,
      physical:{pv_arrays:2,inverters:2,load_nodes:1,battery_nodes:1,grid_nodes:1}
    });
  }
  function normalizeHistory(points){
    if(!Array.isArray(points))return [];
    return points.map(point=>{
      const timestamp=date(point?.timestamp||point?.observed_at_utc||
        point?.recorded_at_utc||point?.timestamp_utc);
      const number=NUM(point?.number??point?.value_decimal);
      return {timestamp,number,unit:String(point?.unit||""),quality:String(point?.quality||"unknown")};
    }).filter(p=>p.timestamp&&p.number!==null)
      .sort((a,b)=>a.timestamp.localeCompare(b.timestamp));
  }
  function historicalValue(points,timestamp,toleranceMs=600000){
    const exact=date(timestamp),t=Date.parse(timestamp);
    if(!exact||!Number.isFinite(t))return null;
    if(toleranceMs===0)return normalizeHistory(points).find(p=>p.timestamp===exact)||null;
    let found=null,best=Infinity;
    for(const p of normalizeHistory(points)){
      const dist=Math.abs(Date.parse(p.timestamp)-t);
      if(dist<=toleranceMs&&dist<best){found=p;best=dist;}
    }
    return found;
  }
  // Deye convention used by the existing archived profile: positive battery
  // power = discharge, negative = charge. Positive grid = import.
  // These are directions of the selected report, not an aggregate measurement.
  function sharedFlow(report,key){
    const reading=report?.reading;
    if(!reading?.valid)return {direction:"none",label:"Немає даних"};
    if(reading.number===0)return {direction:"none",label:"Очікування"};
    const opposing=(report.reports||[]).some(item=>item.valid&&item.number!==0&&
      item.unit===reading.unit&&Math.sign(item.number)!==Math.sign(reading.number));
    const incompatible=(report.reports||[]).some(item=>item.valid&&item.unit!==reading.unit);
    if(opposing||incompatible)return {direction:"unknown",label:"Різні звіти"};
    const incoming=reading.number>0;
    return {direction:incoming?"reverse":"forward",
      label:key==="battery_power"?(incoming?"Розряд":"Заряд"):(incoming?"Імпорт":"Експорт")};
  }
  // Only combine report counters covering exactly the same interval. PV is
  // independent according to the owner topology; common counters count once.
  // This is arithmetic on stored reports, not verification of electrical metering.
  function pairEnergy(reports){
    const keys=["pv_energy_total","load_energy_total","grid_import_total","grid_export_total","battery_charge_total","battery_discharge_total"];
    const result={values:{},window:null,residual:null,complete:false,reason:"Немає порівнюваного інтервалу двох Deye"};
    if(!Array.isArray(reports)||reports.length!==2||reports.some(r=>!r))return result;
    let common=null;
    for(const key of keys){
      const rows=reports.map(report=>(report.counters||[]).find(r=>r.key===key));
      if(rows.some(r=>!r||NUM(r.value)===null||NUM(r.value)<0||Number(r.samples)<2||Number(r.decreases||0)!==0||!["кВт·год","кВт⋅год","кВт*год","kWh","кВт·г"].includes(r.unit)))continue;
      const [a,b]=rows,window=[date(a.first_at),date(a.last_at)];
      if(!window[0]||!window[1]||window[0]>=window[1]||window[0]!==date(b.first_at)||window[1]!==date(b.last_at))continue;
      if(key!=="pv_energy_total"&&NUM(a.value)!==NUM(b.value))continue;
      result.values[key]={number:key==="pv_energy_total"?NUM(a.value)+NUM(b.value):NUM(a.value),first_at:window[0],last_at:window[1]};
      if(common===null)common=window;else if(common!==false&&(common[0]!==window[0]||common[1]!==window[1]))common=false;
    }
    if(common&&keys.every(k=>result.values[k])){
      const n=k=>result.values[k].number;
      result.window=common;result.complete=true;result.reason=null;
      result.residual=n("pv_energy_total")+n("grid_import_total")+n("battery_discharge_total")-n("load_energy_total")-n("grid_export_total")-n("battery_charge_total");
    }else result.reason="Для балансу потрібні всі шість узгоджених лічильників з однаковим початком і кінцем";
    return result;
  }
  // Deterministic read-only episode detector. Never bridge absent data.
  // Duration is between actual observed points, not assumed wall-clock time.
  function evaluateEpisodes(points,rule){
    const threshold=NUM(rule?.threshold),h=NUM(rule?.hysteresis),delay=NUM(rule?.delaySeconds),gap=NUM(rule?.maxGapSeconds);
    if(threshold===null||h===null||h<0||delay===null||delay<0||gap===null||gap<=0||!["above","below"].includes(rule?.direction))throw Error("Некоректні межі правила");
    const normalized=(Array.isArray(points)?points:[]).map(p=>({timestamp:date(p.timestamp||p.observed_at_utc),number:NUM(p.number??p.value_decimal)})).filter(p=>p.timestamp).sort((a,b)=>a.timestamp.localeCompare(b.timestamp)),episodes=[];let pending=null,previous=null;
    const enters=v=>rule.direction==="above"?v>=threshold:v<=threshold;
    const exits=v=>rule.direction==="above"?v<threshold-h:v>threshold+h;
    function finish(end,reason){
      if(!pending)return;
      if(pending.confirmed)episodes.push({start:pending.start,last:pending.last,end:reason==="recovered"?end:null,durationSeconds:(Date.parse(pending.last)-Date.parse(pending.start))/1000,state:reason,extreme:pending.extreme});
      pending=null;
    }
    for(const p of normalized){
      if(p.number===null){finish(previous?.timestamp,"gap");previous=null;continue;}
      if(previous&&(Date.parse(p.timestamp)-Date.parse(previous.timestamp))/1000>gap)finish(previous.timestamp,"gap");
      if(pending&&exits(p.number))finish(p.timestamp,"recovered");
      if(!pending&&enters(p.number))pending={start:p.timestamp,last:p.timestamp,confirmed:delay===0,extreme:p.number};
      if(pending){pending.last=p.timestamp;pending.extreme=rule.direction==="above"?Math.max(pending.extreme,p.number):Math.min(pending.extreme,p.number);if((Date.parse(p.timestamp)-Date.parse(pending.start))/1000>=delay)pending.confirmed=true;}
      previous=p;
    }
    finish(previous?.timestamp,"open");
    return episodes;
  }
  function batteryEstimate(values,capacity,reserve){
    const cap=NUM(capacity),min=NUM(reserve),soc=read(values,"battery_soc"),power=read(values,"battery_power"),soh=read(values,"battery_soh");
    const result={energyKwh:null,hours:null,soh:soh.valid&&soh.unit==="%"&&soh.number>=0&&soh.number<=100?soh.number:null};
    if(cap===null||cap<=0||cap>10000||min===null||min<0||min>100||!soc.valid||soc.unit!=="%"||soc.number<0||soc.number>100)return result;
    result.energyKwh=cap*Math.max(0,soc.number-min)/100;
    if(power.valid&&["кВт","kW"].includes(values?.battery_power?.unit)&&power.number>0&&soc.observed_at_utc&&power.observed_at_utc===soc.observed_at_utc)result.hours=result.energyKwh/power.number;
    return result;
  }
  return Object.freeze({snapshot,read,fmt,pretty,ukTime,normalizeHistory,sharedFlow,pairEnergy,evaluateEpisodes,batteryEstimate,
    historicalValue,differing,POWER_KEYS,SHARED_KEYS});
});
