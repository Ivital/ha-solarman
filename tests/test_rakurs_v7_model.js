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
s=M.snapshot({...cfg,selectedDeviceKey:"deye_2"});
assert.equal(s.reports.load_power.reading.number,6.816,
  "Switch report source, but do not sum or invent a system total");
assert.equal(s.reports.battery_soc.reading.number,73.8);
assert.equal(s.sources[0].reading.number,5.1,
  "Independent PV source must not change when selected shared-device report changes");
console.log("V7_SHARED_REPORT_SELECTION_NO_DOUBLE_COUNT=PASS");
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
assert.equal(s.data_state,"live");
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
assert.equal(M.historicalValue(points,"2026-11-25T01:00:00Z"),null,
  "Must not carry old historical values beyond the bounded time tolerance");
console.log("V7_HISTORY_NEAREST_WITH_TOLERANCE_NO_FABRICATION=PASS");
assert(!M.snapshot(cfg).sources[0].reading.register_verified);
assert.equal(M.read({pv_power:{...row(5),register_address:512}},"pv_power",d1)
  .register_verified,false);
console.log("V7_UNVERIFIED_REGISTERS_REMAIN_UNVERIFIED=PASS");
