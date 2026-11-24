"use strict";
// Run the actual clarity JavaScript with a small DOM simulator; no MySQL/network.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.join(__dirname, "../deploy/rakurs/standalone");
const js = fs.readFileSync(path.join(root, "static/app-parallel-20261124.js"), "utf8");
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
function prepare(mode) {
  const main = new Node("main");
  main.dataset.initialMode = "simulation";
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
  async function fetch(url) {
    fetchCalls.push(url);
    if (mode === "fail" || (mode === "partial" && url.includes("/latest"))) {
      if (mode === "partial" && url.includes("/latest"))
        return {ok: true, json: async () => ({values: {pv_power: latest.pv_power}})};
      throw Error("Unreachable");
    }
    const data = url.includes("/catalog")
      ? {groups: {pv: [{key: "pv_power", label: "PV power"}]}, all_metric_count: 316}
      : url.includes("/status") ? {
        mode: "simulation", displayed_signals: 316, simulated_signals: 316,
        has_real_samples: false
      }
      : url.includes("/signals") ?
        {signals: [{signal_key: "pv_power", label_uk: "PV power", unit: "кВт"}]}
      : url.includes("/auth") ? {authenticated: false}
      : url.includes("/latest") ? {values: latest}
      : url.includes("/parallel/latest") ? {device_key: "inverter_1", values:
          {pv_power: {...latest.pv_power, quality: "good"}}}
      : url.includes("/parallel/events") ? {events: []}
      : url.includes("/parallel") ? {
          expected_inverters: 2, registered_inverters: mode === "two" ? 2 : 0,
          model: "SUN-20K-SG05LP3-EU-SM2", shared_battery: true,
          aggregate_verified: false, battery_soc_percent: null,
          devices: mode === "two" ? [
            {device_key: "inverter_1", model: "SUN-20K-SG05LP3-EU-SM2"},
            {device_key: "inverter_2", model: "SUN-20K-SG05LP3-EU-SM2"}
          ] : []
        }
      : url.includes("/history") ? {signal: {unit: "кВт"}, points: history}
      : url.includes("/modbus-fields") ?
        {count: fields.length, register_count: 815, fields}
      : url.includes("/batches") ? {batches: [
          {started_at_utc:"2026-11-24T19:00:00Z",
           finished_at_utc:"2026-11-24T19:00:00Z",
           received_count:135936,accepted_count:135936,rejected_count:0,
           status:"simulated",error_code:"DEYE_HISTORY_72H_V1"},
          {started_at_utc:"2026-11-24T17:53:54Z",
           finished_at_utc:"2026-11-24T17:53:54Z",
           received_count:624,accepted_count:624,rejected_count:0,
           status:"simulated",error_code:null}
        ]}
      : {devices: [], events: [], batches: []};
    return {ok: true, json: async () => data};
  }
  const sandbox = {
    window: {location: loc, addEventListener: (name, fn) => {events[name] = fn;}},
    location: loc, document: doc, fetch,
    URLSearchParams, Number, Date, String, Array, Set, Math,
    encodeURIComponent, console: {warn() {}}
  };
  vm.runInNewContext(js, sandbox, {filename: "app-events-20261124.js"});
  return {main, nav, events, loc, fetchCalls, ssr};
}
async function settle() {
  await new Promise(resolve => setTimeout(resolve, 60));
}
async function run() {
  assert(html.includes("/static/app-parallel-20261124.js"));
  assert(html.includes("/static/parallel-ui-20261124.css"));
  assert(html.includes("/static/events-ui-20261124.css"));
  assert(html.includes("/static/clarity-ui-20261124.css"));
  assert(html.includes('Показники Deye з бази даних'));
  assert(css.includes(".energy-diagram"));
  assert(css.includes("@media(max-width:760px)"));
  assert(css.includes("prefers-reduced-motion"));
  for (const scenario of ["fail", "partial"]) {
    const app = prepare(scenario);
    await settle();
    assert.equal(app.main.childNodes.length, 1, scenario + ": SSR wiped");
    assert.equal(app.main.childNodes[0].textContent, app.ssr.textContent);
    console.log("CLARITY_SSR_GUARD_" + scenario.toUpperCase() + "=PASS");
  }
  const app = prepare("success");
  await settle();
  assert.equal(byClass(app.main, "kpi").length, 4, "Four headline metrics");
  assert.equal(byClass(app.main, "energy-diagram").length, 1, "Energy flow diagram");
  assert.equal(byClass(app.main, "trend-card").length, 4, "Four history graphs");
  assert.equal(byClass(app.nav, "nav-group-label").length, 3, "Grouped navigation");
  assert(app.fetchCalls.some(x => x.includes("/api/v1/history?key=pv_power")));
  console.log("CLARITY_OVERVIEW=PASS");

  app.loc.hash = "#/trends";
  await app.events.hashchange();
  assert.equal(byClass(app.main, "trend-card").length, 4);
  const buttons = byClass(app.main, "range-control")[0].childNodes;
  assert.equal(buttons.length, 3);
  buttons[2].listeners.click();
  await settle();
  assert(app.fetchCalls.some(x => x.includes("hours=168")));
  console.log("CLARITY_TRENDS_24_72_168=PASS");

  app.loc.hash = "#/modbus";
  await app.events.hashchange();
  const tbody = descendants(app.main).find(x => x.tagName === "tbody");
  assert.equal(tbody.childNodes.length, 100, "initial Modbus page");
  const more = byClass(app.main, "more-button")[0];
  more.listeners.click();
  assert.equal(tbody.childNodes.length, 160, "next Modbus page");
  const search = byClass(app.main, "search-field")[0];
  search.value = "Input 149";
  search.listeners.input();
  assert.equal(tbody.childNodes.length, 1, "search Modbus");
  console.log("CLARITY_MODBUS_FILTER_AND_PAGINATION=PASS");

  app.loc.hash = "#/pv";
  await app.events.hashchange();
  assert.equal(byClass(app.main, "metric").length, 1);
  console.log("CLARITY_SIGNAL_FILTER=PASS");

  app.loc.hash = "#/history";
  await app.events.hashchange();
  assert.equal(byClass(app.main, "history-workspace").length, 1);
  assert.equal(byClass(app.main, "history-summary").length, 1);
  console.log("CLARITY_HISTORY=PASS");

  app.loc.hash = "#/events";
  await app.events.hashchange();
  assert.equal(byClass(app.main, "event-row").length, 2, "Existing DB ingestion batches");
  assert.equal(byClass(app.main, "event-count-card").length, 2,
    "Separate device event and DB load counters");
  const labels = descendants(app.main).map(x => x.textContent || "").join("|");
  assert(labels.includes("Подій обладнання поки немає"));
  assert(labels.includes("Додано історію вимірювань за 72 години"));
  assert(labels.includes("Завантажено початковий тестовий набір"));
  assert(labels.includes("135936") && labels.includes("624"));
  assert(app.fetchCalls.some(x => x.includes("/api/v1/events")));
  assert(app.fetchCalls.some(x => x.includes("/api/v1/batches")));
  console.log("CLARITY_EVENTS_REAL_BATCHES_NO_FAKE_ALARMS=PASS");

  app.loc.hash = "#/parallel";
  await app.events.hashchange();
  assert.equal(byClass(app.main, "parallel-device").length, 2,
    "Must show two expected slots, not invent registered devices");
  assert.equal(byClass(app.main, "parallel-reading").length, 0,
    "Do not fabricate readings while physical ports are unverified");
  const parallelText = descendants(app.main).map(x => x.textContent || "").join("|");
  assert(parallelText.includes("Поточна сумарна потужність: —"));
  assert(parallelText.includes("SOC: —"));
  assert(parallelText.includes("Не зареєстрований"));
  console.log("PARALLEL_TOPOLOGY_UNVERIFIED_NO_FAKE_VALUES=PASS");

  const two = prepare("two");
  await settle();
  two.loc.hash = "#/parallel";
  await two.events.hashchange();
  assert.equal(byClass(two.main, "parallel-device").length, 2);
  assert.equal(byClass(two.main, "parallel-reading").length, 6);
  assert(two.fetchCalls.some(x => x.includes("/parallel/latest?device=inverter_1")));
  assert(two.fetchCalls.some(x => x.includes("/parallel/latest?device=inverter_2")));
  console.log("PARALLEL_DISTINCT_DEVICE_ENDPOINTS=PASS");
}
run().catch(e => {console.error(e.stack);process.exitCode=1;});
