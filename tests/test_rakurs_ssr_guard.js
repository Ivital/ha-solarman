"use strict";
// Browser-behavior regression test without a browser or database:
// failed/incomplete API responses must never erase the SSR MySQL dashboard.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const js = fs.readFileSync(
  path.join(__dirname, "../deploy/rakurs/standalone/static/app-stable-20261124.js"),
  "utf8"
);

class Node {
  constructor(tag) {
    this.tag = tag;
    this.childNodes = [];
    this.dataset = {};
    this.ssr = false;
    this.textContent = "";
    this.clearCalls = 0;
    this.classList = {add() {}};
  }
  appendChild(node) { this.childNodes.push(node); return node; }
  append(...nodes) { this.childNodes.push(...nodes); }
  replaceChildren(...nodes) {
    if (!nodes.length) this.clearCalls += 1;
    this.childNodes = nodes;
  }
  cloneNode(deep) {
    const node = new Node(this.tag);
    node.ssr = this.ssr;
    node.textContent = this.textContent;
    if (deep) node.childNodes = this.childNodes.map(x => x.cloneNode(true));
    return node;
  }
  querySelector(selector) {
    if (selector.includes('Показники Deye з бази даних')) {
      return this.childNodes.find(x => x.ssr) || null;
    }
    return null;
  }
  setAttribute() {}
  addEventListener() {}
}

async function scenario(backend, label) {
  const main = new Node("main");
  main.dataset.initialMode = "simulation";
  const original = new Node("article");
  original.ssr = true;
  original.textContent = "PV=5.10 кВт; LOAD=6.40 кВт; SOC=72%";
  main.childNodes = [original];
  const navigation = new Node("nav");
  const connection = new Node("span");
  const updated = new Node("time");
  const heading = new Node("h1");
  const actions = new Node("div");
  const elements = {content: main, nav: navigation, connection, updated, "view-title": heading};
  const events = {};
  const warnings = [];
  const loc = {search: "", hash: "#/overview", pathname: "/"};
  const ctx = {
    window: {location: loc, addEventListener: (name, callback) => {events[name] = callback;}},
    location: loc,
    document: {
      getElementById: key => elements[key] || null,
      querySelector: selector => selector === ".top-actions" ? actions : null,
      createElement: tag => new Node(tag),
      createTextNode: value => {
        const node = new Node("text");
        node.textContent = value;
        return node;
      },
      body: {classList: {add() {}}},
    },
    URLSearchParams, Date, Number, String, Array, Set, Math, encodeURIComponent,
    console: {warn: message => warnings.push(message)},
    fetch: async url => {
      if (backend === "fail" || url.includes("/latest") && backend === "incomplete") {
        if (backend === "incomplete" && url.includes("/latest")) {
          return {
            ok: true, json: async () => ({
              values: {pv_power: {value_decimal: "5.1", quality: "simulated"}}
            })
          };
        }
        throw new Error("backend temporarily unavailable");
      }
      const body = url.includes("/status")
        ? {mode: "simulation", simulated_signals: 316, has_simulated_samples: true}
        : url.includes("/signals") ? {signals: []}
        : url.includes("/catalog") ? {groups: {}, all_metric_count: 316}
        : {authenticated: false};
      return {ok: true, json: async () => body};
    },
  };
  vm.runInNewContext(js, ctx, {filename: "app-stable-20261124.js"});
  await new Promise(resolve => setTimeout(resolve, 40));

  assert.equal(main.clearCalls, 0, label + ": SSR was cleared");
  assert.equal(main.childNodes.length, 1, label + ": SSR count changed");
  assert.equal(main.childNodes[0].textContent, original.textContent);
  assert.equal(main.childNodes[0].ssr, true);
  assert(warnings.some(message => message.includes("keeping original MySQL readings")));

  // Route away and back: the original DB readings must be restorable too.
  if (backend === "fail") {
    loc.hash = "#/system";
    await events.hashchange();
    loc.hash = "#/overview";
    await events.hashchange();
    assert.equal(main.childNodes.length, 1, label + ": SSR not restored after navigation");
    assert.equal(main.childNodes[0].textContent, original.textContent);
  }
  process.stdout.write("SSR_NO_FLICKER_" + label.toUpperCase() + "=PASS\n");
}

(async () => {
  await scenario("fail", "failed_api");
  await scenario("incomplete", "partial_api");
})().catch(error => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
