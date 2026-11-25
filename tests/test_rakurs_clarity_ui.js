"use strict";
// Run the actual clarity JavaScript with a small DOM simulator; no MySQL/network.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.join(__dirname, "../deploy/rakurs/standalone");
const js = fs.readFileSync(path.join(root, "static/app-parallel-twin-20261125.js"), "utf8");
const css = fs.readFileSync(path.join(root, "static/clarity-ui-20261124.css"), "utf8");
const html = fs.readFileSync(path.join(root, "templates/index.html"), "utf8");

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
    "mode-indicator": new Node("span")
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
    body: {classList: {add() {}}},
  };
  const loc = {pathname: "/", search: "", hash: "#/overview"};
  const events = {};
  const fetchCalls = [];
  const slotKeys = mode === "two" ?
    ["inverter_1", "inverter_2"] :
    ["sim_deye20k_parallel_1", "sim_deye20k_parallel_2"];
  const active = ["two", "seeded", "partial-seeded"].includes(mode);
  const stored = active ? slotKeys.map((device_key, i) =>
    ({device_key, slot_no:i + 1, model:"SUN-20K-SG05LP3-EU-SM2"})) : [];
  async function fetch(url) {
    fetchCalls.push(url);
    if (mode === "fail") throw Error("Unreachable");
    if ((mode === "partial" || mode === "partial-seeded") &&
        url.includes("/latest?") && !url.includes("/parallel/"))
      return {ok:true, json:async () => ({values: {pv_power:latest.pv_power}})};
    const data =
      url.includes("/catalog") ?
        {groups:{pv:[{key:"pv_power",label:"PV power"}]},all_metric_count:316}
      : url.includes("/status") ?
        {mode:"real",displayed_signals:316,registered_devices:3,
         has_real_samples:true,collector_active:false}
      : url.includes("/signals") ?
        {signals:[{signal_key:"pv_power",label_uk:"PV power",unit:"кВт"}]}
      : url.includes("/auth") ? {authenticated:false}
      : url.includes("/parallel/latest?") ?
        {device_key:slotKeys[0],values:latest}
      : url.includes("/parallel/history?") ? {points:history}
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
      : url.includes("/history?") ? {signal:{unit:"кВт"},points:history}
      : url.includes("/modbus-fields") ?
        {count:fields.length,register_count:815,fields}
      : url.includes("/batches") ? {batches:[
          {started_at_utc:"2026-11-24T19:00:00Z",
           finished_at_utc:"2026-11-24T19:00:00Z",
           received_count:135936,accepted_count:135936,rejected_count:0,
           status:"simulated",error_code:"DEYE_HISTORY_72H_V1"},
          {started_at_utc:"2026-11-24T17:53:54Z",
           finished_at_utc:"2026-11-24T17:53:54Z",
           received_count:624,accepted_count:624,rejected_count:0,
           status:"simulated",error_code:null}
        ]}
      : url.includes("/devices") ?
        {devices:[
          {device_key:"simulated_deye_sg05lp3",kind:"simulation",model:"Deye"},
          {device_key:"sim_deye20k_parallel_1",kind:"simulation",model:"Deye"},
          {device_key:"sim_deye20k_parallel_2",kind:"simulation",model:"Deye"}
        ]}
      : {events:[],devices:[],batches:[]};
    return {ok:true,json:async () => data};
  }
  const sandbox = {
    window:{location:loc,addEventListener:(name,fn)=>{events[name]=fn;}},
    location:loc,document:doc,fetch,
    URLSearchParams,Number,Date,String,Array,Set,Math,
    encodeURIComponent,console:{warn(){}}
  };
  vm.runInNewContext(js,sandbox,{filename:"app-parallel-twin-20261125.js"});
  return {main,nav,events,loc,fetchCalls,ssr,elements};
}
async function settle() {
  await new Promise(resolve => setTimeout(resolve,60));
}
async function run() {
  assert(html.includes("/static/app-parallel-twin-20261125.js"));
  assert(html.includes("/static/parallel-twin-20261125.css"));
  assert(html.includes("/static/events-ui-20261124.css"));
  assert(html.includes("/static/clarity-ui-20261124.css"));
  assert(css.includes(".energy-diagram"));
  assert(css.includes("@media(max-width:760px)"));
  assert(css.includes("prefers-reduced-motion"));
  assert(!/Тестові дані|СИМУЛЯЦІЯ|ДЕМО/i.test(html));
  for(const scenario of ["fail","partial"]) {
    const app=prepare(scenario);
    await settle();
    assert.equal(app.main.childNodes[0].textContent,app.ssr.textContent);
    console.log("CLARITY_SSR_GUARD_"+scenario.toUpperCase()+"=PASS");
  }
  const app=prepare("seeded");
  await settle();
  assert.equal(byClass(app.main,"kpi").length,4);
  assert.equal(byClass(app.main,"energy-diagram").length,1);
  assert.equal(byClass(app.main,"parallel-home-device").length,2);
  assert.equal(byClass(app.main,"trend-card").length,4);
  assert.equal(byClass(app.nav,"nav-group-label").length,3);
  assert.equal(app.elements["mode-indicator"].textContent,"Дані MySQL");
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
    String(x.textContent || "").includes("Останні показники · Deye №2")));
  console.log("CLARITY_SWITCH_DEYE_1_2=PASS");
  console.log("CLARITY_UNIFIED_MYSQL_OVERVIEW=PASS");

  app.loc.hash="#/trends";
  await app.events.hashchange();
  assert.equal(byClass(app.main,"trend-card").length,4);
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

  const empty=prepare("empty");
  await settle();
  empty.loc.hash="#/parallel";
  await empty.events.hashchange();
  assert.equal(byClass(empty.main,"parallel-device").length,0);
  console.log("PARALLEL_EMPTY_MYSQL_NO_FAKE_DEVICES=PASS");
}
run().catch(e=>{console.error(e.stack);process.exitCode=1;});
