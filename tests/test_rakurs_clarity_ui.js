"use strict";
// Run the actual clarity JavaScript with a small DOM simulator; no MySQL/network.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.join(__dirname, "../deploy/rakurs/standalone");
const js = fs.readFileSync(path.join(root, "static/app-parallel-twin-20261125.js"), "utf8");
const css = fs.readFileSync(path.join(root, "static/clarity-ui-20261124.css"), "utf8");
const chartCss = fs.readFileSync(path.join(root, "static/parallel-twin-20261125.css"), "utf8");
const energyBoardJs = fs.readFileSync(path.join(root,"static/rakurs-energy-board-20261126.js"),"utf8");
const energyBoardCss = fs.readFileSync(path.join(root,"static/rakurs-energy-board-20261126.css"),"utf8");
const html = fs.readFileSync(path.join(root, "templates/index.html"), "utf8");
const reference = JSON.parse(fs.readFileSync(path.join(root, "static/register-reference-v1053.json"), "utf8"));
const colorCss = fs.readFileSync(path.join(root, "static/rakurs-color-20261125.css"), "utf8");
const pagesCss = fs.readFileSync(path.join(root, "static/rakurs-pages-20261126.css"), "utf8");
const pageTheme = fs.readFileSync(path.join(root, "static/rakurs-page-theme-20261126.js"), "utf8");
const loginHtml = fs.readFileSync(path.join(root, "templates/login.html"), "utf8");
const deyeHtml = fs.readFileSync(path.join(root, "templates/deye_server.html"), "utf8");

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.childNodes = [];
    this.attributes = {};
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.className = "";
    this.ssr = false;
    this.namespaceURI = "http://www.w3.org/2000/svg";
    this.listeners = {};
    this.classList = {add() {}};
  }
  appendChild(child) { this.childNodes.push(child); return child; }
  append(...children) { this.childNodes.push(...children); }
  replaceChildren(...children) { this.childNodes = children; }
  cloneNode(deep) {
    const n = new Node(this.tagName);
    n.textContent = this.textContent;
    n.ssr = this.ssr;
    n.className = this.className;
    n.attributes = {...this.attributes};
    if (deep) n.childNodes = this.childNodes.map(x => x.cloneNode(true));
    return n;
  }
  setAttribute(key, val) { this.attributes[key] = String(val); }
  addEventListener(key, fn) { this.listeners[key] = fn; }
  querySelector(selector) {
    if (selector.includes('Показники Deye з бази даних')) {
      return descendants(this).find(x => x.ssr) || null;
    }
    return null;
  }
}
function descendants(node) {
  return [node, ...node.childNodes.flatMap(child => descendants(child))];
}
function byClass(node, className) {
  return descendants(node).filter(x => (x.className || "").split(/\s+/).includes(className));
}
const measurements = {
  pv_power: [5.1, "кВт"], load_power: [6.4, "кВт"],
  battery_soc: [72, "%"], grid_power: [0, "кВт"],
  battery_power: [1.3, "кВт"], inverter_temperature: [37.6, "°C"],
  pv_energy_today: [21.4, "кВт·год"], load_energy_today: [34.3, "кВт·год"]
};
const latest = Object.fromEntries(Object.entries(measurements).map(([k, [v, unit]]) => [
  k, {value_decimal: String(v), unit, quality: "simulated",
    observed_at_utc: "2026-11-24T17:53:54Z"}
]));
const history = Array.from({length: 30}, (_, i) => ({
  value_decimal: String(i % 10 + 1),
  observed_at_utc: "2026-11-23T" + String(i % 24).padStart(2, "0") + ":00:00Z",
  quality: "simulated"
}));
const fields = Array.from({length: 160}, (_, i) => ({
  field_key: "reg_" + i, field_name: "Input " + i, group_name: "Power",
  registers_json: JSON.stringify([i]), value_decimal: String(i + 1),
  value_text: null, unit: "W"
}));
function prepare(mode = "seeded") {
  const main = new Node("main");
  main.dataset.initialMode = "real";
  const ssr = new Node("div");
  ssr.ssr = true;
  ssr.textContent = "5.1 6.4 72";
  main.childNodes = [ssr];
  const nav = new Node("nav");
  const elements = {
    content: main, nav, connection: new Node("span"),
    updated: new Node("span"), "view-title": new Node("h1"),
    "mode-indicator": new Node("span"),
    "device-select": new Node("select"),
    "theme-toggle": new Node("button"),
    "refresh-data": new Node("button")
  };
  const body = new Node("body");
  const storage = new Map();
  const localStorage = {
    getItem: key => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, String(value))
  };
  const actions = new Node("div");
  const doc = {
    getElementById: key => elements[key] || null,
    querySelector: selector => selector === ".top-actions" ? actions : null,
    createElement: tag => new Node(tag),
    createElementNS: (_uri, tag) => new Node(tag),
    createTextNode: value => {
      const n = new Node("text"); n.textContent = value; return n;
    },
    body,
  };
  const loc = {pathname: "/", search: "", hash: "#/overview"};
  const events = {};
  const fetchCalls = [];
  const slotKeys = mode === "two" ?
    ["inverter_1", "inverter_2"] :
    ["sim_deye20k_parallel_1", "sim_deye20k_parallel_2"];
  const active = ["two", "seeded", "partial-seeded", "single", "admin"].includes(mode);
  const stored = active ? slotKeys.map((device_key, i) =>
    (mode === "seeded"
      ? {device_key, model:"SUN-20K-SG05LP3-EU-SM2"}
      : {device_key, slot_no:i + 1, model:"SUN-20K-SG05LP3-EU-SM2"})) : [];
  async function fetch(url, options = {}) {
    fetchCalls.push(url);
    if (url.includes("/static/register-reference-v1053.json")) return {ok:true,json:async()=>reference};
    if (mode === "fail") throw Error("Unreachable");
    if ((mode === "partial" || mode === "partial-seeded") &&
        url.includes("/latest?") && !url.includes("/parallel/"))
      return {ok:true, json:async () => ({values: {pv_power:latest.pv_power}})};
    const data =
      url.includes("/catalog") ?
        {groups:{pv:[{key:"pv_power",label:"PV power"}]},all_metric_count:316}
      : url.includes("/status") ?
        {mode:"real",displayed_signals:316,registered_devices:2,
         has_real_samples:true,collector_active:false}
      : url.includes("/signals") ?
        {signals:[{signal_key:"pv_power",label_uk:"PV power",unit:"кВт"}]}
      : url.includes("/auth") ? mode === "admin" ? {authenticated:true,username:"admin",role:"admin",csrf:"token"} : {authenticated:false}
      : url.includes("/parallel/latest?") ?
        {device_key:slotKeys[0],values:latest}
      : url.includes("/parallel/history?") ? {points:mode==="single"?history.slice(0,1):history}
      : url.includes("/parallel/modbus-fields?") ?
        {device_key:slotKeys[0],fields:[
          {field_key:"p3_0001",field_name:"PV one",unit:"кВт",
           value_decimal:"5.1",value_text:null},
          {field_key:"p3_0002",field_name:"Temperature",unit:"°C",
           value_decimal:"32",value_text:null}
        ],count:2}
      : url.includes("/parallel/raw?") ?
        {total:815,poll:{poll_uid:"poll_20261125"},raw_registers:[
          {function_code:3,register_address:100,raw_uint16:65535},
          {function_code:3,register_address:101,raw_uint16:0}
        ]}
      : url.includes("/parallel/events?") ? {events:[]}
      : url.includes("/parallel") ?
        {model:"SUN-20K-SG05LP3-EU-SM2",expected_inverters:2,
         registered_inverters:stored.length,devices:stored,aggregate_verified:false}
      : url.includes("/latest?") ? {values:latest}
      : url.includes("/history?") ? {signal:{unit:"кВт"},points:mode==="no-recent-history"?[]:mode==="single"?history.slice(0,1):history}
      : url.includes("/config") ? {available:true,enabled:false,interval_seconds:60,worker_online:false,
          devices:stored.map((d,i)=>({...d,id:i+2,display_name:"Deye №"+(i+1),enabled:false,
            transport:"unconfigured",function_code:3,register_count:1,timeout_ms:1500}))}
      : url.includes("/modbus-fields") ?
        {count:fields.length,register_count:815,fields}
      : url.includes("/batches") ? {batches:[
          {started_at_utc:"2026-11-24T19:00:00Z",
           finished_at_utc:"2026-11-24T19:00:00Z",
           received_count:135936,accepted_count:135936,rejected_count:0,
           status:"generated",error_code:"DEYE_HISTORY_72H_V1"},
          {started_at_utc:"2026-11-24T17:53:54Z",
           finished_at_utc:"2026-11-24T17:53:54Z",
           received_count:624,accepted_count:624,rejected_count:0,
           status:"generated",error_code:null}
        ]}
      : url.includes("/devices") ?
        {devices:[
          {device_key:"sim_deye20k_parallel_1",kind:"inverter",model:"Deye"},
          {device_key:"sim_deye20k_parallel_2",kind:"inverter",model:"Deye"}
        ]}
      : {events:[],devices:[],batches:[]};
    return {ok:true,json:async () => data};
  }
  const sandbox = {
    window:{location:loc,addEventListener:(name,fn)=>{events[name]=fn;}},
    location:loc,document:doc,fetch,localStorage,
    URLSearchParams,Number,Date,String,Array,Set,Math,
    encodeURIComponent,console:{warn(){}}
  };
  vm.runInNewContext(energyBoardJs,{window:sandbox.window},{filename:"rakurs-energy-board-20261126.js"});
  vm.runInNewContext(js,sandbox,{filename:"app-parallel-twin-20261125.js"});
  return {main,nav,events,loc,fetchCalls,ssr,elements,body,storage};
}
async function settle() {
  await new Promise(resolve => setTimeout(resolve,60));
}
async function run() {
  assert(html.includes("/static/app-parallel-twin-20261125.js"));
  assert(html.includes("/static/parallel-twin-20261125.css"));
  assert(html.includes("/static/events-ui-20261124.css"));
  assert(html.includes("/static/rakurs-color-20261125.css?v=rakurs-flow-v4-20261126"));
  assert(html.includes('id="theme-toggle"'));
  assert(html.includes('data-theme="light"'));
  assert(colorCss.includes(".kpi-solar") && colorCss.includes(".kpi-battery"));
  for(const selector of [".sidebar",".parallel-fact",".parallel-reading",".parallel-raw-row",
    ".parallel-field-row",".event-row",".event-count-card",".settings-field",
    ".shared-bus-inverter",".shared-bus-result",".trend-card",".history-workspace",
    ".data-table",".register-lead",".quick-link",".insight-strip",".energy-node"]) {
    assert(colorCss.includes(selector), "Themed surface missing: "+selector);
  }
  assert(colorCss.includes("body[data-theme=light]") && colorCss.includes("body[data-theme=dark]"));
  assert(pagesCss.includes(".auth-card") && pagesCss.includes(".bar"));
  assert(pageTheme.includes("rakurs_theme"));
  const externalBody=new Node("body");
  const externalButton=new Node("button");
  const externalStore=new Map();
  vm.runInNewContext(pageTheme,{
    document:{body:externalBody,readyState:"complete",getElementById:key=>key==="page-theme-toggle"?externalButton:null},
    localStorage:{getItem:key=>externalStore.get(key)||null,setItem:(key,value)=>externalStore.set(key,String(value))}
  });
  assert.equal(externalBody.attributes["data-theme"],"light");
  externalButton.listeners.click();
  assert.equal(externalBody.attributes["data-theme"],"dark");
  assert.equal(externalStore.get("rakurs_theme"),"dark");
  console.log("CLARITY_THEME_ALL_PAGES_TOGGLE=PASS");
  assert(loginHtml.includes("/static/rakurs-pages-20261126.css"));
  assert(deyeHtml.includes("/static/rakurs-pages-20261126.css"));
  assert(loginHtml.includes('id="page-theme-toggle"'));
  assert(deyeHtml.includes('id="page-theme-toggle"'));
  assert(html.includes("energy-board-ssr") && html.includes("insight-strip"));
  assert(html.indexOf("energy-board-ssr")<html.indexOf("cards cards-main"),
    "System diagram must be first, before KPI cards");
  assert(html.includes("/static/rakurs-energy-board-20261126.js"));
  assert(html.includes("/static/rakurs-energy-board-20261126.css"));
  assert(html.includes("rakurs-energy-board-20261126.css?v=rakurs-mobile-v5-20261126"));
  assert(html.includes("rakurs-energy-board-20261126.js?v=rakurs-mobile-v5-20261126"));
  assert(html.indexOf("rakurs-energy-board-20261126.js")<
    html.indexOf("app-parallel-twin-20261125.js"),
    "Energy diagram must load before dashboard app");
  assert(energyBoardCss.includes(".energy-board-map") && 
    energyBoardCss.includes("prefers-reduced-motion"));
  console.log("CLARITY_SYSTEM_DIAGRAM_FIRST_ASSETS=PASS");
  assert(html.includes('id="refresh-data"'));
  assert(html.includes("dashboard-toolbar-ssr"));
  assert(colorCss.includes(".dashboard-options") && colorCss.includes(".nav-search") &&
    colorCss.includes(".refresh-data"));
  assert(colorCss.includes("@keyframes rakurs-ui-reveal"));
  assert(colorCss.includes(".card.kpi,.parallel-home-device,.insight-item"));
  assert(colorCss.includes("@media(prefers-reduced-motion:reduce)"));
  console.log("CLARITY_INTERACTIVE_CARDS_AND_RESPECT_REDUCED_MOTION=PASS");
  console.log("CLARITY_PERSONAL_DASHBOARD_ASSETS=PASS");
  console.log("CLARITY_LIGHT_ALL_COMPONENTS_AND_EXTRA_PAGES=PASS");
  assert(colorCss.includes("prefers-reduced-motion"));
  assert(colorCss.includes(".trend-grid[hidden]") &&
         colorCss.includes(".mode-indicator.mode-archive"));
  assert(html.includes('mode-indicator mode-archive'));
  console.log("CLARITY_ARCHIVE_STATUS_CSS=PASS");
  assert.equal(reference.counts.document_addresses,755);
  assert.equal(reference.counts.profile_addresses,815);
  assert.equal(reference.counts.shared_addresses,290);
  assert.equal(reference.entries.length,1280);
  assert.equal(reference.compatibility,"NOT_VERIFIED_FOR_SUN20K_SG05LP3_EU_SM2");
  console.log("CLARITY_COLORFUL_THEME_AND_REFERENCE_COUNTS=PASS");
  assert(html.includes("/static/clarity-ui-20261124.css"));
  assert(css.includes(".energy-diagram"));
  assert(css.includes("@media(max-width:760px)"));
  assert(css.includes("prefers-reduced-motion"));
  assert(html.includes('href="/static/parallel-twin-20261125.css?v=rakurs-ux4-20261126-1"'));
  assert(html.includes('src="/static/app-parallel-twin-20261125.js?v=rakurs-flow-v4-20261126"'));
  assert(/\.trend-section\.full-trends \.trend-grid\s*\{\s*grid-template-columns:\s*repeat\(2,minmax\(0,1fr\)\)/.test(chartCss),
    "The dedicated charts route must have two compact columns");
  assert(/@media\(max-width:760px\)/.test(chartCss),
    "Charts should stack on small screens");
  assert(chartCss.includes(".trend-card.trend-network") &&
         chartCss.includes(".trend-network .chart polyline"),
    "Network chart must not inherit nested trend-grid layout");
  assert(chartCss.includes(".history-chart .chart") &&
         chartCss.includes(".parallel-device-chart .chart") &&
         chartCss.includes("width:100%") &&
         chartCss.includes("max-width:none"),
    "All SVG chart surfaces must occupy available card width");
  console.log("CLARITY_CORRECT_CHART_LAYOUT=PASS");
  assert(!/Тестові дані|СИМУЛЯЦІЯ|ДЕМО/i.test(html));
  const boardWindow={};
  vm.runInNewContext(energyBoardJs,{window:boardWindow},{filename:"rakurs-energy-board-20261126.js"});
  const separate=boardWindow.RakursEnergyBoard.snapshot({
    devices:[
      {device_key:"inv1",slot_no:1,values:{pv_power:latest.pv_power,
        battery_soc:latest.battery_soc}},
      {device_key:"inv2",slot_no:2,values:{pv_power:{...latest.pv_power,value_decimal:"7.7"},
        battery_soc:{...latest.battery_soc,value_decimal:"73.8"}}}
    ],
    selectedDeviceKey:"inv1",mode:"system",
    status:{collector_active:false,physical_topology_verified:false}});
  assert.equal(separate.devices.length,2);
  assert.equal(separate.confirmed,false);
  assert.equal(separate.values.pv_power.value_decimal,"5.1",
    "System must show independently scoped values, NOT a fabricated sum");
  assert.equal(boardWindow.RakursEnergyBoard.metric(separate.values,"battery_soc"),72,
    "Never sum shared battery SOC across inverters");
  assert.equal(boardWindow.RakursEnergyBoard.metric(separate.values,"load_power"),null);
  assert(!energyBoardJs.includes("comparisonWire("),
    "The UI must not redraw physically shared nodes as independent physical branches");
  assert(!energyBoardJs.includes("is-comparison-wire"));

  assert.equal(boardWindow.RakursEnergyBoard.flowState("grid",2).direction,"←");
  assert.equal(boardWindow.RakursEnergyBoard.flowState("grid",-2).direction,"→");
  assert.equal(boardWindow.RakursEnergyBoard.flowState("grid",0).active,false);
  assert.equal(boardWindow.RakursEnergyBoard.flowState("battery",-1.3).caption,"Заряд");
  assert.equal(boardWindow.RakursEnergyBoard.flowState("battery",1.3).caption,"Розряд");
  const noDevice=boardWindow.RakursEnergyBoard.snapshot({
    devices:[],fallbackValues:latest,selectedSlotNo:1,status:{collector_active:true,physical_topology_verified:true}});
  assert.equal(noDevice.devices.length,0,"Unavailable MySQL API must not invent a device");
  assert.equal(noDevice.confirmed,false,"No actual registered devices may ever show LIVE");
  console.log("CLARITY_FLOW_DIRECTIONS_AND_NO_FABRICATED_DEVICES=PASS");
  const freshNow=new Date().toISOString();
  const freshValues={pv_power:{...latest.pv_power,observed_at_utc:freshNow}};
  const statusConfirmed={collector_active:true,physical_topology_verified:true};
  const mixedFresh=boardWindow.RakursEnergyBoard.snapshot({
    devices:[
      {device_key:"inv1",slot_no:1,values:freshValues},
      {device_key:"inv2",slot_no:2,values:latest}
    ],mode:"system",status:statusConfirmed});
  assert.equal(mixedFresh.confirmed,false,"Stale second Deye must disable system animation");
  const bothFresh=boardWindow.RakursEnergyBoard.snapshot({
    devices:[
      {device_key:"inv1",slot_no:1,values:freshValues},
      {device_key:"inv2",slot_no:2,values:freshValues}
    ],mode:"system",status:statusConfirmed});
  assert.equal(bothFresh.confirmed,true,"Both physically confirmed, fresh devices can be animated");
  console.log("CLARITY_LIVE_REQUIRES_TWO_FRESH_REGISTERED_DEVICES=PASS");
  const onlySecond=boardWindow.RakursEnergyBoard.snapshot({
    devices:[{device_key:"inv2",slot_no:2,values:{pv_power:latest.pv_power}}],
    selectedDeviceKey:"inv2",selectedSlotNo:2,status:{collector_active:false}});
  const host=new Node("div");
  const doc={createElement:tag=>new Node(tag)};
  boardWindow.RakursEnergyBoard.render(host,{document:doc,
    devices:onlySecond.devices,selectedDeviceKey:"inv2",selectedSlotNo:2,
    status:{collector_active:false},mode:"system"});
  const pv1=byClass(host,"energy-board-source")[0];
  assert(pv1.childNodes.some(x=>String(x.textContent||"")==="☀"));
  assert(descendants(pv1).some(x=>String(x.textContent||"")==="—"),
    "Missing first Deye must never inherit Deye 2 PV");
  console.log("CLARITY_NO_SHARED_BATTERY_AGGREGATION_OR_INVENTED_DEVICE=PASS");
  const compareHost=new Node("div");
  const sampleDeye1={...latest,grid_power:{...latest.grid_power,value_decimal:"2"},
    battery_power:{...latest.battery_power,value_decimal:"1.3"}};
  const sampleDeye2={...latest,load_power:{...latest.load_power,value_decimal:"6.816"},
    grid_power:{...latest.grid_power,value_decimal:"-2"},
    battery_power:{...latest.battery_power,value_decimal:"-1.385"},
    battery_soc:{...latest.battery_soc,value_decimal:"73.8"}};
  boardWindow.RakursEnergyBoard.render(compareHost,{document:doc,
    devices:[
      {device_key:"inv1",slot_no:1,values:sampleDeye1},
      {device_key:"inv2",slot_no:2,values:sampleDeye2}
    ],selectedDeviceKey:"inv2",mode:"system",
    status:{collector_active:false,physical_topology_verified:false}});
  const sharedNodes=byClass(compareHost,"energy-board-node");
  assert.equal(sharedNodes.length,3,"There is ONE physical load, ONE grid and ONE battery");
  assert.equal(byClass(compareHost,"energy-board-source").length,2,
    "Only PV arrays are separate, one for each Deye");
  assert.equal(byClass(compareHost,"energy-board-wire").length,5,
    "Two PV links and exactly three common-node links");
  const outputZone=byClass(compareHost,"energy-board-outputs")[0];
  const branches=byClass(outputZone,"energy-board-output-row");
  assert(outputZone && branches.length===3,
    "One common output zone must contain all three bus branches");
  const keys=["house","battery","grid"];
  branches.forEach((branch,index)=>{
    assert(branch.className.includes("output-"+keys[index]));
    assert.equal(branch.childNodes.length,2,
      "Physical output row must keep its wire adjacent to its destination card");
    assert(branch.childNodes[0].className.includes("wire-"+keys[index]));
    assert(branch.childNodes[1].className.includes("energy-board-"+keys[index]));
    assert.equal(byClass(branch,"energy-board-wire").length,1);
    assert.equal(byClass(branch,"energy-board-node").length,1);
  });
  assert.equal(byClass(compareHost,"source-wires")[0].childNodes.length,2);
  console.log("CLARITY_MOBILE_PAIRED_BRANCH_DOM=PASS");
  assert.equal(byClass(compareHost,"energy-board-wire-lane").length,0,
    "One physical common node must NOT split into per-Deye physical flow lanes");
  assert.equal(byClass(compareHost,"energy-board-reading-row").length,0,
    "Two telemetry reports must NOT be represented as two physical loads");
  assert.equal(byClass(compareHost,"energy-board-shared-tag").length,0,
    "No repeated common/shared physical node tags in card faces");
  const house=sharedNodes.find(node=>node.className.includes("energy-board-house"));
  assert.equal(byClass(house,"energy-board-node-value").length,1,
    "Common load must have ONE displayed reference figure");
  assert(byClass(house,"energy-board-node-value")[0].textContent.includes("6,816"),
    "System mode uses clearly labeled selected Deye report; NOT an invented system total");
  assert(descendants(house).some(node=>
    String(node.textContent||"").includes("Архів Deye №2")));
  assert(descendants(house).some(node=>
    String(node.textContent||"").includes("покази Deye різні")));
  house.listeners.click();
  const details=byClass(compareHost,"energy-board-inspector-description")[0].textContent;
  assert(details.includes("Deye №1: 6,4")&&details.includes("Deye №2: 6,816"),
    "Both inverter reports remain available on click, as diagnostics of ONE shared load");
  const battery=sharedNodes.find(node=>node.className.includes("energy-board-battery"));
  assert.equal(byClass(battery,"energy-board-node-value").length,1);
  assert(descendants(battery).some(node=>String(node.textContent||"").includes("SOC 73,8")));
  battery.listeners.click();
  const batteryInfo=byClass(compareHost,"energy-board-inspector-description")[0].textContent;
  assert(batteryInfo.includes("SOC 72")&&batteryInfo.includes("SOC 73,8"));
  const grid=sharedNodes.find(node=>node.className.includes("energy-board-grid"));
  grid.listeners.click();
  const sharedGridWire=byClass(compareHost,"wire-grid");
  assert.equal(sharedGridWire.length,1,"Shared power grid has only ONE connection");
  assert(sharedGridWire[0].className.includes("no-flow"),
    "Opposite report signs cannot be rendered as a known common-grid direction");
  assert.equal(byClass(sharedGridWire[0],"energy-board-direction")[0].textContent,"—");
  assert(descendants(grid).some(node=>
    String(node.textContent||"").includes("Напрямок невідомий")));
  const selectedOneHost=new Node("div");
  boardWindow.RakursEnergyBoard.render(selectedOneHost,{document:doc,
    devices:[
      {device_key:"inv1",slot_no:1,values:sampleDeye1},
      {device_key:"inv2",slot_no:2,values:sampleDeye2}
    ],selectedDeviceKey:"inv1",mode:"1",
    status:{collector_active:false,physical_topology_verified:false}});
  const selectedHouse=byClass(selectedOneHost,"energy-board-house")[0];
  assert(byClass(selectedHouse,"energy-board-node-value")[0].textContent.includes("6,4"));
  assert.equal(byClass(selectedOneHost,"energy-board-node").length,3);
  assert.equal(byClass(compareHost,"energy-board-legend").length,1);
  assert.equal(byClass(compareHost,"energy-board-flow-track").length,5);
  const gridTrack=byClass(sharedGridWire[0],"energy-board-flow-track")[0];
  assert(gridTrack);
  assert.equal(byClass(sharedGridWire[0],"energy-board-flow-particle").length,0,
    "No movement for conflicting grid directions");
  const batteryWire=byClass(compareHost,"wire-battery")[0];
  assert(batteryWire.className.includes("is-reverse")===false,
    "Selected Deye №2 reports charge, not discharge");
  assert.equal(byClass(batteryWire,"energy-board-flow-particle").length,0,
    "Opposite battery directions cannot be animated as one confirmed common flow");
  const oneBatteryWire=byClass(selectedOneHost,"wire-battery")[0];
  assert.equal(byClass(oneBatteryWire,"energy-board-flow-particle").length,3,
    "A single Deye archive snapshot may illustrate its reported flow direction");
  batteryWire.listeners.click();
  assert.equal(batteryWire.attributes["aria-pressed"],"true",
    "Flow track is clickable and selects the battery");
  assert(byClass(compareHost,"energy-board-inspector-title")[0].textContent.includes("Акумулятор"));
  console.log("CLARITY_ACCESSIBLE_STREAM_INTERACTION_AND_NO_FAKE_FLOW=PASS");
  const wholeText=descendants(compareHost).map(node=>String(node.textContent||"")).join(" ");
  assert(!wholeText.includes("13,216")&&!wholeText.includes("2,685"),
    "Do not fabricate shared-node totals from separate inverter reports");
  assert(energyBoardCss.includes(".energy-board-flow-track"));
  assert(energyBoardCss.includes("@keyframes rakurs-stream"));
  assert(energyBoardCss.includes("@keyframes rakurs-stream-reverse"));
  assert(!energyBoardCss.includes(".energy-board-wire-lane"));
  assert(energyBoardCss.includes("@media(max-width:600px)"));
  assert(energyBoardCss.includes("prefers-reduced-motion"));
  assert(energyBoardCss.includes("energy-board-flow-head"),"Visible direction tips");
  console.log("CLARITY_STREAM_RESPONSIVE_AND_REDUCED_MOTION=PASS");
  const mobileLayout=energyBoardCss.slice(energyBoardCss.indexOf("/* MOBILE V5:"));
  assert(mobileLayout.includes("grid-template-rows:48px!important"),
    "PV connectors must have a single 48px row, not inherited 2-row 100% tracks");
  assert(mobileLayout.includes("height:48px!important"),
    "Mobile PV wire container must have an explicit compact height");
  assert(mobileLayout.includes("min-height:0!important;height:auto!important"),
    "Mobile map must release inherited grid min-height");
  assert(mobileLayout.includes(".energy-board-output-row"),
    "Mobile output node and wire must share one dedicated grid row");
  assert(mobileLayout.includes("grid-template-columns:clamp(53px,19vw,68px)"),
    "Mobile output arrows must not consume half the viewport");
  assert(mobileLayout.includes("@keyframes rakurs-stream-down"),
    "PV particles must travel vertically toward the inverter hub");
  assert(mobileLayout.includes(".energy-board-outputs::before") &&
    mobileLayout.includes(".energy-board-outputs::after"),
    "Shared bus must visually connect its three independent branches");
  assert(mobileLayout.includes("@media(prefers-reduced-motion:reduce)"),
    "OS reduced-motion preference must suppress decorative particles");
  console.log("CLARITY_MOBILE_360PX_NO_GAPS_AND_CONNECTED_FLOWS=PASS");
  console.log("CLARITY_ONE_COMMON_LOAD_GRID_BATTERY_TWO_SEPARATE_PV=PASS");
  console.log("CLARITY_SHARED_TELEMETRY_DIAGNOSTICS_NO_AGGREGATION=PASS");
  for(const scenario of ["fail","partial"]) {
    const app=prepare(scenario);
    await settle();
    assert.equal(app.main.childNodes[0].textContent,app.ssr.textContent);
    console.log("CLARITY_SSR_GUARD_"+scenario.toUpperCase()+"=PASS");
  }
  const app=prepare("seeded");
  await settle();
  assert.equal(app.body.attributes["data-theme"],"light");
  const themeButton=app.elements["theme-toggle"];
  themeButton.listeners.click();
  assert.equal(app.body.attributes["data-theme"],"dark");
  assert.equal(app.storage.get("rakurs_theme"),"dark");
  themeButton.listeners.click();
  assert.equal(app.body.attributes["data-theme"],"light");
  assert.equal(app.storage.get("rakurs_theme"),"light");
  console.log("CLARITY_THEME_SWITCH_BOTH_WAYS=PASS");
  assert.equal(byClass(app.main,"kpi").length,4);
  assert.equal(byClass(app.main,"energy-board").length,1);
  assert.equal(app.main.childNodes[0].className,"energy-board-host");
  assert.equal(byClass(app.main,"energy-board-source").length,2);
  assert.equal(byClass(app.main,"energy-board-node").length,3);
  assert.equal(byClass(app.main,"energy-board-mode").length,3);
  assert.equal(byClass(app.main,"energy-board-flag")[0].textContent,
    "◷ Архівний зріз — не онлайн");
  assert.equal(byClass(app.main,"energy-board")[0].className.includes("is-live"),false);
  console.log("CLARITY_ENERGY_FLOW_ON_TOP_ARCHIVE_NOT_LIVE=PASS");
  const energyMotion=byClass(app.main,"energy-board-motion")[0];
  const energySpeed=byClass(app.main,"energy-board-speed")[0];
  assert(energyMotion&&energySpeed);
  assert.equal(byClass(app.main,"energy-board-wire").length,5);
  assert.equal(byClass(app.main,"energy-board-flow-track").length,5);
  assert.equal(byClass(app.main,"energy-board-flow-particle").length,12,
    "Two active PV, house and battery have illustrative particles; grid zero has none");
  assert(byClass(app.main,"energy-board")[0].className.includes("is-animated"));
  assert(byClass(app.main,"energy-board-visual-label")[0].textContent.includes("архівним"),
    "Animated archive must be clearly labeled as historical visualization");
  energyMotion.listeners.click();
  assert.equal(energyMotion.attributes["aria-pressed"],"false");
  assert(byClass(app.main,"energy-board")[0].className.includes("is-paused"));
  assert(!byClass(app.main,"energy-board")[0].className.includes("is-animated"));
  energySpeed.listeners.click();
  assert(byClass(app.main,"energy-board")[0].className.includes("speed-fast"));
  energySpeed.listeners.click();
  assert(byClass(app.main,"energy-board")[0].className.includes("speed-slow"));
  energySpeed.listeners.click();
  assert(byClass(app.main,"energy-board")[0].className.includes("speed-normal"));
  energyMotion.listeners.click();
  assert.equal(energyMotion.attributes["aria-pressed"],"true");
  assert(byClass(app.main,"energy-board")[0].className.includes("is-animated"));
  console.log("CLARITY_ARCHIVE_FLOW_ANIMATION_PAUSE_AND_SPEED=PASS");
  assert.equal(byClass(app.main,"energy-board-shared-tag").length,0);
  for(const label of ["Будинок","Акумулятор","Мережа"]){
    assert(byClass(app.main,"energy-board-node-name").some(item=>item.textContent===label));
  }
  assert(!byClass(app.main,"energy-board-context")[0].textContent.includes("спільн"));
  console.log("CLARITY_CLEAN_ENERGY_LABELS=PASS");
  assert.equal(byClass(app.main,"insight-item").length,3);
  assert.equal(byClass(app.main,"quick-link").length,3);
  console.log("CLARITY_NEW_HOME_HIERARCHY=PASS");
  assert.equal(byClass(app.main,"dashboard-preferences").length,1);
  const customButton=byClass(app.main,"dashboard-config-button")[0];
  assert.equal(customButton.attributes["aria-expanded"],"false");
  customButton.listeners.click();
  assert.equal(customButton.attributes["aria-expanded"],"true");
  assert.equal(byClass(app.main,"dashboard-options")[0].hidden,false);
  const allMetrics=byClass(app.main,"dashboard-preset").find(x=>x.textContent==="Усі 8");
  allMetrics.listeners.click();
  assert.equal(byClass(app.main,"kpi").length,8);
  assert.equal(app.storage.get("rakurs.dashboard.metrics.v1").split(",").length,8);
  const dashboardCheckboxes=byClass(app.main,"dashboard-check");
  assert.equal(dashboardCheckboxes.length,8);
  dashboardCheckboxes[7].checked=false;
  dashboardCheckboxes[7].listeners.change();
  assert.equal(byClass(app.main,"kpi").length,7);
  assert(!app.storage.get("rakurs.dashboard.metrics.v1").includes("load_energy_today"));
  const moveDown=byClass(app.main,"dashboard-option-row")[0];
  const reorder=byClass(moveDown,"dashboard-order-button")[1];
  reorder.listeners.click();
  assert.equal(app.storage.get("rakurs.dashboard.metrics.v1").split(",")[0],"load_power");
  const defaults=byClass(app.main,"dashboard-preset").find(x=>x.textContent==="Основні 4");
  defaults.listeners.click();
  assert.equal(byClass(app.main,"kpi").length,4);
  assert.equal(app.storage.get("rakurs.dashboard.metrics.v1"),"pv_power,load_power,battery_soc,grid_power");
  customButton.listeners.click();
  assert.equal(byClass(app.main,"dashboard-options")[0].hidden,true);
  console.log("CLARITY_KPI_PREFERENCES_PERSIST_AND_REORDER=PASS");
  const navSearch=byClass(app.nav,"nav-search")[0];
  assert(navSearch);
  navSearch.value="Modbus";
  navSearch.listeners.input();
  assert.equal(app.nav.childNodes.filter(x=>x.tagName==="a"&&!x.hidden).length,1);
  assert.equal(byClass(app.nav,"nav-group-label").filter(x=>!x.hidden).length,1);
  navSearch.value="неіснуючий-розділ";
  navSearch.listeners.input();
  assert.equal(byClass(app.nav,"nav-empty")[0].hidden,false);
  navSearch.listeners.keydown({key:"Escape"});
  assert.equal(app.nav.childNodes.filter(x=>x.tagName==="a"&&!x.hidden).length,15);
  console.log("CLARITY_SIDEBAR_SEARCH_AND_EMPTY=PASS");
  const reloadButton=app.elements["refresh-data"];
  assert(reloadButton.listeners.click);
  const prevRequests=app.fetchCalls.length;
  await reloadButton.listeners.click();
  await settle();
  assert(app.fetchCalls.length>prevRequests);
  assert.equal(reloadButton.disabled,false);
  assert.equal(reloadButton.textContent,"↻ Оновити");
  console.log("CLARITY_READONLY_MANUAL_REFRESH=PASS");
  assert.equal(byClass(app.main,"energy-board-map").length,1);
  assert(descendants(app.main).some(x=>String(x.textContent||"").includes("На дату останнього запису")));
  assert(descendants(app.main).some(x=>String(x.textContent||"").includes("Без обміну")));
  assert(!descendants(app.main).some(x=>String(x.textContent||"").includes("За сьогодні")));
  console.log("CLARITY_ARCHIVED_DAY_AND_ZERO_FLOW=PASS");
  assert.equal(byClass(app.main,"parallel-home-device").length,2);
  assert.equal(byClass(app.main,"trend-card").length,4);
  assert.equal(byClass(app.nav,"nav-group-label").length,3);
  assert.equal(app.elements["mode-indicator"].textContent,"Архів MySQL");
  assert.equal(app.elements["mode-indicator"].className,"mode-indicator mode-archive");
  assert(app.fetchCalls.some(x=>x.includes("/api/v1/history?key=pv_power")));
  const selectors = byClass(app.main, "parallel-open-link")
    .filter(item => item.tagName === "button");
  assert.equal(selectors.length, 2);
  assert.equal(selectors[0].attributes["aria-pressed"], "true");
  selectors[1].listeners.click();
  await settle();
  assert(app.fetchCalls.some(x =>
    x.includes("/latest?keys=") &&
    x.includes("&device=sim_deye20k_parallel_2")));
  assert(descendants(app.main).some(x =>
    String(x.textContent || "").includes("Ключові показники · Deye №2")));
  console.log("CLARITY_SWITCH_DEYE_1_2=PASS");
  const globalPicker=app.elements["device-select"];
  assert.equal(globalPicker.childNodes.length,2);
  assert.equal(globalPicker.childNodes[0].textContent,"Deye №1");
  assert.equal(globalPicker.childNodes[1].textContent,"Deye №2",
    "The missing server slot_no must not make both labels Deye №1");
  assert.equal(globalPicker.value,"sim_deye20k_parallel_2",
    "Global selector follows Deye 2 selected on the home card");
  // Home-card selector chooses Deye 2, while global control can switch back.
  globalPicker.value="sim_deye20k_parallel_1";
  await globalPicker.listeners.change();
  await settle();
  assert(descendants(app.main).some(x =>
    String(x.textContent||"").includes("Ключові показники · Deye №1")));
  console.log("CLARITY_GLOBAL_DEYE_PICKER=PASS");
  console.log("CLARITY_UNIFIED_MYSQL_OVERVIEW=PASS");
  const nodes=byClass(app.main,"energy-board-node");
  const gridNode=nodes.find(x=>x.className.includes("energy-board-grid"));
  assert(gridNode);
  gridNode.listeners.click();
  assert.equal(gridNode.attributes["aria-pressed"],"true");
  assert(byClass(app.main,"energy-board-inspector")[0].childNodes.some(x=>
    String(x.textContent||"").includes("Мережа")));
  assert.equal(byClass(app.main,"energy-board-wire").filter(x=>x.className.includes("is-live")).length,0);
  assert(byClass(app.main,"energy-board-wire").some(x=>
    x.className.includes("wire-grid")&&x.className.includes("no-flow")),
    "Zero grid import must not display an active flow");
  const flowTabs=byClass(app.main,"energy-board-mode");
  flowTabs.find(x=>x.textContent==="Deye №2").listeners.click();
  await settle();
  assert.equal(app.elements["device-select"].value,"sim_deye20k_parallel_2");
  assert(byClass(app.main,"energy-board-context").some(x=>String(x.textContent||"").includes("Deye №2")));
  byClass(app.main,"energy-board-mode").find(x=>x.textContent==="Уся система").listeners.click();
  await settle();
  assert.equal(byClass(app.main,"energy-board-mode").find(x=>
    x.textContent==="Уся система").attributes["aria-pressed"],"true");
  console.log("CLARITY_ENERGY_FLOW_NODE_INSPECTION_AND_MODES=PASS");

  app.loc.hash="#/trends";
  await app.events.hashchange();
  assert.equal(byClass(app.main,"trend-card").length,4);
  assert.equal(byClass(app.main,"full-trends").length,1,"Charts route remains a compact analytics layout");
  const chartTiles = byClass(app.main,"trend-card");
  assert.equal(chartTiles.filter(tile => tile.className.split(/\s+/).includes("trend-grid")).length,0,
    "No chart tile may reuse the trend-grid layout class");
  const networkTile=chartTiles.find(tile => tile.className.split(/\s+/).includes("trend-network"));
  assert(networkTile, "Network chart needs its own style class");
  assert.equal(byClass(networkTile,"trend-content").length,1,
    "Network plot content must occupy its own full-width tile");
  const ranges=byClass(app.main,"range-control")[0].childNodes;
  assert.equal(ranges.length,3);
  ranges[2].listeners.click();
  await settle();
  assert(app.fetchCalls.some(x=>x.includes("hours=168")));
  console.log("CLARITY_TRENDS_24_72_168=PASS");

  app.loc.hash="#/modbus";
  await app.events.hashchange();
  const tbody=descendants(app.main).find(x=>x.tagName==="tbody");
  assert.equal(tbody.childNodes.length,100);
  byClass(app.main,"more-button")[0].listeners.click();
  assert.equal(tbody.childNodes.length,160);
  const search=byClass(app.main,"search-field")[0];
  search.value="Input 149";
  search.listeners.input();
  assert.equal(tbody.childNodes.length,1);
  console.log("CLARITY_MODBUS_SEARCH_AND_PAGING=PASS");

  app.loc.hash="#/registers";
  await app.events.hashchange();
  assert.equal(byClass(app.main,"register-workspace").length,1);
  assert.equal(byClass(app.main,"register-row").length,100);
  const regSearch=byClass(app.main,"search-field")[0];
  regSearch.value="0x0001";regSearch.listeners.input();
  assert.equal(byClass(app.main,"register-row").length,1);
  console.log("CLARITY_ALL_SOURCE_REGISTER_AUDIT=PASS");

  app.loc.hash="#/pv";
  await app.events.hashchange();
  assert.equal(byClass(app.main,"metric").length,1);
  console.log("CLARITY_SIGNAL_FILTER=PASS");

  app.loc.hash="#/history";
  await app.events.hashchange();
  assert.equal(byClass(app.main,"history-workspace").length,1);
  assert.equal(byClass(app.main,"history-summary").length,1);
  console.log("CLARITY_HISTORY=PASS");

  app.loc.hash="#/events";
  await app.events.hashchange();
  assert.equal(byClass(app.main,"event-row").length,2);
  const eventCopy=descendants(app.main).map(x=>x.textContent||"").join("|");
  assert(eventCopy.includes("Додано історію вимірювань за 72 години"));
  assert(eventCopy.includes("Завантажено записи MySQL"));
  assert(!/симуляц|тестов|демо/i.test(eventCopy));
  console.log("CLARITY_MYSQL_EVENT_LOG=PASS");

  app.loc.hash="#/system";
  await app.events.hashchange();
  const systemText=descendants(app.main).map(x=>x.textContent||"").join("|");
  assert(systemText.includes("Пристрої у базі"));
  assert(systemText.includes("Показники у базі"));
  assert(systemText.includes("2"), "Only two Deye devices remain after cleanup");
  assert(!systemText.includes("sim_deye20k"));
  assert(!systemText.includes("simulated"));
  console.log("CLARITY_MYSQL_SYSTEM=PASS");

  app.loc.hash="#/parallel";
  await app.events.hashchange();
  assert.equal(byClass(app.main,"parallel-device").length,2,
    "Exactly two real-mode stored Deye cards");
  assert.equal(byClass(app.main,"parallel-reading").length,12,
    "Six stored readings for each Deye");
  assert(app.fetchCalls.some(x=>x.includes("/parallel/latest?device=sim_deye20k_parallel_1")));
  assert(app.fetchCalls.some(x=>x.includes("/parallel/latest?device=sim_deye20k_parallel_2")));
  assert(app.fetchCalls.some(x=>x.includes("/parallel/history?device=sim_deye20k_parallel_1")));
  assert(app.fetchCalls.some(x=>x.includes("/parallel/modbus-fields?device=sim_deye20k_parallel_2")));
  const parallelText=descendants(app.main).map(x=>x.textContent||"").join("|");
  assert(parallelText.includes("5,1"));
  assert(parallelText.includes("815"));
  assert(parallelText.includes("Поля Modbus"));
  assert(!/симуляц|тестов|демо|синтетич/i.test(parallelText));
  const rawButton=byClass(app.main,"parallel-raw-viewer")[0].childNodes
    .find(x=>x.tagName==="button");
  await rawButton.listeners.click();
  assert.equal(byClass(app.main,"parallel-raw-row").length,2);
  const fieldButton=byClass(app.main,"parallel-field-viewer")[0].childNodes
    .find(x=>x.tagName==="button");
  fieldButton.listeners.click();
  assert.equal(byClass(app.main,"parallel-field-row").length,2);
  console.log("PARALLEL_TWO_DEYE_ALL_MYSQL_VALUES=PASS");

  const partial=prepare("partial-seeded");
  await settle();
  assert(partial.main.childNodes.some(x=>x.ssr&&x.textContent===partial.ssr.textContent));
  assert.equal(byClass(partial.main,"parallel-home-device").length,2);
  console.log("PARALLEL_SURVIVES_INCOMPLETE_LEGACY_API=PASS");

  const emptyTrends=prepare("no-recent-history");
  await settle();
  const summary=byClass(emptyTrends.main,"trend-empty-summary")[0];
  assert(summary && !summary.hidden);
  assert.equal(byClass(emptyTrends.main,"trend-grid")[0].hidden,true);
  const next=byClass(summary,"trend-empty-button")[0];
  assert(next && next.textContent==="Показати 3 доби");
  next.listeners.click();
  await settle();
  assert(emptyTrends.fetchCalls.some(url=>url.includes("/history?key=pv_power")&&url.includes("hours=72")));
  console.log("CLARITY_EMPTY_TRENDS_COMPACT_AND_72H=PASS");

  const onePoint=prepare("single");
  await settle();
  onePoint.loc.hash="#/history";
  await onePoint.events.hashchange();
  assert(descendants(onePoint.main).some(x => x.tagName==="circle"),
    "A single archived point must be visible on the SVG");
  console.log("CLARITY_SINGLE_POINT_CHART=PASS");

  const admin=prepare("admin");
  await settle();
  admin.loc.hash="#/settings";
  await admin.events.hashchange();
  assert(byClass(admin.main,"settings-save").length>=3,
    "Admin can manage polling and at least two inverters");
  assert(byClass(admin.main,"settings-field").length>=20);
  console.log("CLARITY_ADMIN_SETTINGS_UI=PASS");

  const empty=prepare("empty");
  await settle();
  empty.loc.hash="#/parallel";
  await empty.events.hashchange();
  assert.equal(byClass(empty.main,"parallel-device").length,0);
  console.log("PARALLEL_EMPTY_MYSQL_NO_FAKE_DEVICES=PASS");
}
run().catch(e=>{console.error(e.stack);process.exitCode=1;});
