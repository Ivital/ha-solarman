"use strict";
const assert=require("node:assert/strict");
const M=require("../deploy/rakurs/standalone/static/rakurs-v7-model.js");
const when="2026-11-25T00:16:18.000Z";
const row=(value,unit="кВт",timestamp=when)=>({
  value_decimal:value,unit,observed_at_utc:timestamp
});
const d1={slot_no:1,device_key:"deye_1",values:{
  pv_power:row(5.1),load_power:row(6.4),battery_power:row(-1.3),
  battery_soc:row(72,"%"),grid_power:row(0)
}};
const d2={slot_no:2,device_key:"deye_2",values:{
  pv_power:row(5.432),load_power:row(6.816),
  battery_power:row(-1.385),battery_soc:row(73.8,"%"),grid_power:row(0)
}};
const cfg={devices:[d1,d2],selectedDeviceKey:"deye_1",selectedSlotNo:1,
  status:{collector_active:false,physical_topology_verified:false,
    register_semantics_verified:false},now:Date.parse(when)+86_400_000};
let s=M.snapshot(cfg);
assert.equal(s.data_state,"archive");
assert.equal(s.devices.length,2);
assert.deepEqual(s.physical,{pv_arrays:2,inverters:2,load_nodes:1,
  battery_nodes:1,grid_nodes:1});
assert.equal(s.sources[0].reading.number,5.1);
assert.equal(s.sources[1].reading.number,5.432);
assert.equal(s.reports.load_power.reading.number,6.4);
assert.equal(s.reports.battery_soc.reading.number,72);
assert.equal(s.reports.grid_power.reading.number,0);
assert.equal(M.fmt(s.reports.grid_power.reading),"0 кВт");
assert.equal(s.reports.load_power.verified_system_total,false);
assert.equal(s.show_aggregate_pv,false);
assert.equal(s.reports.load_power.conflict,true);
assert.equal(s.reports.battery_soc.conflict,true);
assert(s.warnings.some(x=>x.includes("відрізняються")));
assert.equal(s.read_only,true);
console.log("V7_TOPOLOGY_TWO_PV_ONE_LOAD_ONE_BATTERY_ONE_GRID=PASS");
const firstSelection=s;
s=M.snapshot({...cfg,devices:[d2,d1],selectedDeviceKey:"deye_2"});
assert.deepEqual(s.reports,firstSelection.reports);
assert.deepEqual(s.sources,firstSelection.sources);
assert.equal(s.sharedDevice.device_key,d1.device_key);
console.log("V7_SHARED_NODES_INDEPENDENT_OF_DEVICE_PICKER=PASS");
let empty=M.snapshot({devices:[],fallbackValues:{},now:Date.parse(when)});
assert.equal(empty.sources.length,2);
assert(empty.sources.every(x=>x.device===null&&!x.reading.valid));
assert.equal(empty.data_state,"missing");
assert.equal(M.fmt(empty.reports.load_power.reading),"—");
const zero=M.read({grid_power:row("0")},"grid_power",d1);
const missing=M.read({grid_power:{value_decimal:null,unit:"кВт"}},
  "grid_power",d1);
assert.equal(zero.valid,true);
assert.equal(zero.number,0);
assert.equal(missing.valid,false);
assert.equal(M.fmt(missing),"—");
assert.equal(M.read({pv_power:row("not a number")},"pv_power",d1).valid,false);
assert.equal(M.read({pv_power:row("Infinity")},"pv_power",d1).valid,false);
console.log("V7_MISSING_IS_NOT_ZERO_AND_NO_SYNTHETIC_DEVICE=PASS");
s=M.snapshot({...cfg,now:Date.parse(when)+30000,
  status:{collector_active:true,physical_topology_verified:true,
    register_semantics_verified:false}});
assert.equal(s.data_state,"archive",
  "Realtime must not be claimed without firmware/register verification");
s=M.snapshot({...cfg,now:Date.parse(when)+30000,
  status:{collector_active:true,physical_topology_verified:true,
    register_semantics_verified:true}});
assert.equal(s.data_state,"archive","Global flags cannot validate unverified or conflicting readings");
const verified=Object.fromEntries(Object.entries(d1.values).map(([key,val])=>[key,{...val,register_verified:true}]));
const good=[{...d1,values:verified},{...d2,values:verified}];
const liveStatus={collector_active:true,physical_topology_verified:true,register_semantics_verified:true};
assert.equal(M.snapshot({...cfg,devices:good,now:Date.parse(when)+30000,status:liveStatus}).data_state,"live");
const staleOne={...good[1],values:{...verified,pv_power:{...verified.pv_power,observed_at_utc:"2026-11-24T00:00:00Z"}}};
assert.equal(M.snapshot({...cfg,devices:[good[0],staleOne],now:Date.parse(when)+30000,status:liveStatus}).data_state,"archive");
s=M.snapshot({...cfg,now:Date.parse(when)+86_400_000,
  status:{collector_active:true,physical_topology_verified:true,
    register_semantics_verified:true}});
assert.equal(s.data_state,"archive","Stale samples cannot be live");
console.log("V7_ALL_PHYSICAL_AND_FRESHNESS_GATES_REQUIRED=PASS");
const points=[
 {value_decimal:"0",unit:"кВт",observed_at_utc:"2026-11-25T00:00:00Z"},
 {value_decimal:"3.2",unit:"кВт",observed_at_utc:"2026-11-25T00:10:00Z"},
 {value_decimal:null,unit:"кВт",observed_at_utc:"2026-11-25T00:20:00Z"},
 {value_decimal:"NaN",unit:"кВт",observed_at_utc:"2026-11-25T00:30:00Z"}
];
assert.deepEqual(M.normalizeHistory(points).map(p=>p.number),[0,3.2]);
assert.equal(M.historicalValue(points,"2026-11-25T00:11:00Z").number,3.2);
assert.equal(M.historicalValue(M.normalizeHistory(points),"2026-11-25T00:11:00Z").number,3.2,
  "Already-normalized history must be safe to use for V7 SVG snapshot playback");
assert.equal(M.historicalValue(points,"2026-11-25T01:00:00Z"),null,
  "Must not carry old historical values beyond the bounded time tolerance");
console.log("V7_HISTORY_NEAREST_WITH_TOLERANCE_NO_FABRICATION=PASS");
assert(!M.snapshot(cfg).sources[0].reading.register_verified);
assert.equal(M.read({pv_power:{...row(5),register_address:512}},"pv_power",d1)
  .register_verified,false);
console.log("V7_UNVERIFIED_REGISTERS_REMAIN_UNVERIFIED=PASS");

const failedD2={...d2,values:{},telemetry_error:true};
const partial=M.snapshot({...cfg,devices:[d1,failedD2],
  fallbackValues:{load_power:row(999),battery_soc:row(99,"%")}});
assert.equal(partial.sources[1].reading.valid,false);
assert.equal(partial.reports.load_power.reading.number,6.4);
assert(partial.warnings.some(x=>x.includes("Не вдалося прочитати покази Deye №2")));
const failedSelected=M.snapshot({...cfg,devices:[{...d1,values:{},telemetry_error:true},d2],
  selectedDeviceKey:"deye_1",fallbackValues:{load_power:row(888)}});
assert.equal(failedSelected.reports.load_power.reading.valid,false,
  "Legacy unscoped fallback must never impersonate missing selected-device readings");
const unidentified=M.snapshot({devices:[],fallbackValues:{load_power:row(8)}});
assert.equal(unidentified.reports.load_power.reading.scope,"unscoped");
assert(unidentified.warnings.some(x=>x.includes("Немає підтвердженої прив'язки")));
console.log("V7_PARTIAL_API_FAILURE_NEVER_RELABELS_FALLBACK_AS_DEVICE=PASS");

const generatedDevice={...d1,values:Object.fromEntries(Object.entries(d1.values).map(([key,val])=>[key,{...val,quality:"generated",register_verified:true}]))};
const generated=M.snapshot({...cfg,devices:[generatedDevice,good[1]],now:Date.parse(when)+30000,status:liveStatus});
assert.equal(generated.generated,true);
assert.equal(generated.data_state,"archive");
assert.notEqual(generated.sources[0].reading.quality,"generated");
assert.equal(M.normalizeHistory([{timestamp:when,number:1,quality:"generated"}])[0].quality,"generated");
console.log("V7_ARCHIVE_PROVENANCE_RETAINED=PASS");

assert.deepEqual(M.sharedFlow(M.snapshot(cfg).reports.battery_power,"battery_power"),{direction:"forward",label:"Заряд"});
const discharge=[{...d1,values:{...d1.values,battery_power:row(1.38)}},{...d2,values:{...d2.values,battery_power:row(1.4)}}];
assert.deepEqual(M.sharedFlow(M.snapshot({...cfg,devices:discharge}).reports.battery_power,"battery_power"),{direction:"reverse",label:"Розряд"});
assert.equal(M.sharedFlow(M.snapshot({...cfg,devices:[d1,discharge[1]]}).reports.battery_power,"battery_power").direction,"unknown");
assert.equal(M.sharedFlow(M.snapshot(cfg).reports.grid_power,"grid_power").direction,"none");
assert.equal(M.sharedFlow(M.snapshot({}).reports.battery_power,"battery_power").label,"Немає даних");
console.log("RAKURS_SIGNED_BATTERY_DIRECTION=PASS");

const micro=[row(1,"кВт","2026-11-25T00:16:18.000100Z"),row(2,"кВт","2026-11-25T00:16:18.000786Z")];
assert.equal(M.historicalValue(micro,"2026-11-25T00:16:18.000786Z",0).number,2);
assert.equal(M.historicalValue(micro,"2026-11-25T00:16:18.000101Z",0),null);
const counters=["pv_energy_total","load_energy_total","grid_import_total","grid_export_total","battery_charge_total","battery_discharge_total"];
const makeEnergy=values=>({counters:counters.map((key,i)=>({key,value:values[i],samples:3,decreases:0,unit:"кВт·год",first_at:"2026-11-25T00:00:00Z",last_at:"2026-11-25T01:00:00Z"}))});
const energy1=makeEnergy([4,10,2,1,1,2]),energy2=makeEnergy([4,10,2,1,1,2]);
const balance=M.pairEnergy([energy1,energy2]);assert(balance.complete);assert.equal(balance.values.pv_energy_total.number,8);assert.equal(balance.values.load_energy_total.number,10);assert.equal(balance.residual,0);
const wrong=makeEnergy([4,11,2,1,1,2]);assert.equal(M.pairEnergy([energy1,wrong]).complete,false);assert.equal(M.pairEnergy([energy1,null]).complete,false);
wrong.counters[0].first_at="2026-11-25T00:00:00.000001Z";assert(!M.pairEnergy([energy1,wrong]).values.pv_energy_total);
console.log("RAKURS_EXACT_MICROSECOND_AND_SHARED_COUNTER_BALANCE=PASS");

const episodePoints=[0,1,2,3,4,5,10,11,12].map((minute,i)=>row([4,11,12,9,8,7,12,13,14][i],"кВт",new Date(Date.UTC(2026,10,25,0,minute)).toISOString()));
const episodes=M.evaluateEpisodes(episodePoints,{direction:"above",threshold:10,hysteresis:2,delaySeconds:60,maxGapSeconds:90});
assert.equal(episodes.length,2);assert.equal(episodes[0].state,"recovered");assert.equal(episodes[0].durationSeconds,180);assert.equal(episodes[1].state,"open");assert.equal(episodes[1].durationSeconds,120);
const broken=M.evaluateEpisodes([row(11,"кВт","2026-11-25T00:00:00Z"),row(12,"кВт","2026-11-25T00:10:00Z")],{direction:"above",threshold:10,hysteresis:1,delaySeconds:60,maxGapSeconds:90});assert.equal(broken.length,0);
console.log("RAKURS_ALARM_EPISODES_DELAY_HYSTERESIS_AND_GAP=PASS");

assert.equal(M.evaluateEpisodes([row(11,"кВт","2026-11-25T00:00:00Z"),row(null,"кВт","2026-11-25T00:01:00Z"),row(12,"кВт","2026-11-25T00:02:00Z")],{direction:"above",threshold:10,hysteresis:1,delaySeconds:60,maxGapSeconds:180}).length,0);

const estimate=M.batteryEstimate({battery_soc:row(72,"%"),battery_power:row(2,"кВт"),battery_soh:row(98,"%")},"20","20");assert.equal(estimate.energyKwh,10.4);assert.equal(estimate.hours,5.2);assert.equal(estimate.soh,98);assert.equal(M.batteryEstimate({battery_soc:row(72,"%"),battery_power:row(-2,"кВт")},"20","20").hours,null);assert.equal(M.batteryEstimate({battery_soc:row(72,"%")},"","20").energyKwh,null);
