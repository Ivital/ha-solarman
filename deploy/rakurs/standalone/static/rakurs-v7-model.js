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
    return Number.isFinite(d.getTime())?d.toISOString():null;
  }
  function read(values,key,device){
    const item=values&&typeof values==="object"?values[key]:undefined;
    const number=NUM(item?.value_decimal);
    const measured_at_utc=date(item?.observed_at_utc);
    let unit=String(item?.unit||((key==="battery_soc")?"%":"кВт"));
    // Never convert watts implicitly; retain the reported unit and its scale.
    const registerAddress=item?.register_address??item?.modbus_address??null;
    return Object.freeze({
      key,number,unit,
      valid:number!==null,
      observed_at_utc:measured_at_utc,
      device_key:device?.device_key||null,
      slot_no:device?.slot_no||null,
      register_address:registerAddress,
      sign_verified:item?.sign_verified===true&&item?.register_verified===true,
      register_verified:item?.register_verified===true,
      scope:"device_report"
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
    if(ctx.isLive&&fresh(item.observed_at_utc,ctx.now))return "live";
    return "archive";
  }
  function differing(a,b){
    if(!a?.valid||!b?.valid)return false;
    if(a.unit!==b.unit)return true;
    // This is a difference between independent device reports, not evidence of fault.
    const delta=Math.abs(a.number-b.number),scale=Math.max(Math.abs(a.number),Math.abs(b.number),1);
    return delta>Math.max(.15,.05*scale);
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
    const values=(selected&&Object.keys(selectedValues).length)?selectedValues:
      config.fallbackValues||{};
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
    const reportedFresh=devices.length===2&&devices.every(d=>{
      const timestamps=Object.values(d.values||{}).map(r=>date(r?.observed_at_utc)).filter(Boolean);
      return timestamps.length>0&&fresh(timestamps.sort().at(-1),now);
    });
    // All three physical gates + firmware/register semantics must be verified.
    const isLive=Boolean(config.status?.collector_active===true&&
      config.status?.physical_topology_verified===true&&
      config.status?.register_semantics_verified===true&&reportedFresh);
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
    for(const key of SHARED_KEYS)if(reports[key].conflict)
      warnings.push("Покази вузла «"+LABELS[key]+"» від двох Deye відрізняються");
    const alignedPv=sources.every(x=>x.reading.valid&&x.reading.unit===sources[0].reading.unit&&
      x.reading.observed_at_utc&&sources[0].reading.observed_at_utc&&
      Math.abs(Date.parse(x.reading.observed_at_utc)-Date.parse(sources[0].reading.observed_at_utc))<=60000);
    // Even with aligned PV timestamps, a verified aggregate is still required.
    return Object.freeze({
      devices,selected,source:"MySQL",data_state:isLive?"live":validReadings.length?"archive":"missing",
      updated_at_utc:last,show_aggregate_pv:false,pv_time_aligned:alignedPv,
      sources,reports,warnings,read_only:true,
      physical:{pv_arrays:2,inverters:2,load_nodes:1,battery_nodes:1,grid_nodes:1}
    });
  }
  function normalizeHistory(points){
    if(!Array.isArray(points))return [];
    return points.map(point=>{
      const timestamp=date(point?.observed_at_utc||point?.recorded_at_utc||
        point?.timestamp_utc||point?.timestamp);
      const number=NUM(point?.value_decimal);
      return {timestamp,number,unit:String(point?.unit||"")};
    }).filter(p=>p.timestamp&&p.number!==null)
      .sort((a,b)=>a.timestamp.localeCompare(b.timestamp));
  }
  function historicalValue(points,timestamp,toleranceMs=600000){
    const t=Date.parse(timestamp);
    if(!Number.isFinite(t))return null;
    let found=null,best=Infinity;
    for(const p of normalizeHistory(points)){
      const dist=Math.abs(Date.parse(p.timestamp)-t);
      if(dist<=toleranceMs&&dist<best){found=p;best=dist;}
    }
    return found;
  }
  return Object.freeze({snapshot,read,fmt,pretty,ukTime,normalizeHistory,
    historicalValue,differing,POWER_KEYS,SHARED_KEYS});
});
