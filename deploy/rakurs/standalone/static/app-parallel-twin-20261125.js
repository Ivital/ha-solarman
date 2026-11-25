"use strict";
// Rakurs standalone: the ONLY data API is this app's /api/v1/* backed by MySQL.
const base = "/api/v1";
const DEMO_MODE = false;
const FORCE_REAL_MODE = false;
const views = [
  ["overview", "Огляд", "◈"],
  ["pv", "Сонце", "☀"],
  ["battery", "Акумулятор", "▣"],
  ["grid", "Мережа", "⌁"],
  ["load", "Навантаження", "⌂"],
  ["inverter", "Інвертор", "▤"],
  ["parallel", "Паралельна система", "⧉"],
  ["trends", "Графіки", "▥"],
  ["history", "Історія", "◷"],
  ["events", "Події", "◇"],
  ["archive", "Архів", "▦"],
  ["modbus", "Modbus", "▤"],
  ["system", "Система", "⚙"],
  ["settings", "Керування", "⚙"]
];
const state = { catalog: null, status: null, verified: [], route: "overview", cycle: 0,
  selectedDeviceKey: null, selectedSlotNo: 1, role: "viewer", csrf: null };
const main = document.getElementById("content");
const navigation = document.getElementById("nav");
// Root HTML contains authoritative MySQL readings. Never erase those readings
// until the browser has fetched the same readings successfully.
const SSR_MODE = main ? main.dataset.initialMode : "";
const SSR_PRESENT = Boolean(main && main.querySelector(
  '.cards[aria-label="Показники Deye з бази даних"]'));
const SSR_ORIGINAL_NODES = SSR_PRESENT ?
  Array.from(main.childNodes).map(node => node.cloneNode(true)) : [];
function restoreServerReadings() {
  if (SSR_ORIGINAL_NODES.length) {
    main.replaceChildren(...SSR_ORIGINAL_NODES.map(node => node.cloneNode(true)));
  }
}

function element(tag, className, content) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (content !== undefined && content !== null) item.textContent = String(content);
  return item;
}
function add(parent, ...nodes) { for (const node of nodes) parent.appendChild(node); return parent; }
function clear(node) { node.replaceChildren(); }
function time(value) {
  if (!value) return "Немає часу вимірювання";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "Некоректна дата" :
    d.toLocaleString("uk-UA", { timeZone: "Europe/Kyiv", year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function number(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("uk-UA", {maximumFractionDigits: 4}) : "—";
}
function createEmpty(title, detail) {
  const box = element("div", "empty");
  add(box, element("strong", "", title), element("span", "", detail));
  return box;
}
function section(title, caption) {
  const row = element("div", "section-header");
  add(row, element("h2", "", title), element("span", "subtle", caption || ""));
  main.appendChild(row);
  return row;
}
function message(title, detail, warning) {
  const box = element("div", "banner" + (warning ? " warning" : ""));
  const content = element("div", "");
  add(content, element("strong", "", title), element("p", "", detail));
  add(box, element("div", "banner-icon", warning ? "!" : "◎"), content);
  main.appendChild(box);
}
async function api(path, options = {}) {
  const scoped = /^\/(latest|history|modbus-fields)(\?|$)/.test(path);
  const suffix = scoped && state.selectedDeviceKey ?
    (path.includes("?") ? "&" : "?") +
    "device=" + encodeURIComponent(state.selectedDeviceKey) : "";
  const url = base + path + suffix;
  const response = await fetch(url, {cache:"no-store",credentials:"same-origin",
    method:options.body ? "POST" : "GET",
    headers:options.body ? {"Content-Type":"application/json",
      "X-CSRF-Token":state.csrf || ""} : {},
    body:options.body ? JSON.stringify(options.body) : undefined});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "Помилка API RAKURS (" + response.status + ")");
  }
  return response.json();
}
function createLogout(auth) {
  const actions = document.querySelector(".top-actions");
  if (!actions || !auth || !auth.authenticated || !auth.csrf) return;
  const form = document.createElement("form");
  form.method = "post";
  form.action = "/logout";
  form.className = "logout-form";
  const csrf = document.createElement("input");
  csrf.type = "hidden";
  csrf.name = "csrf";
  csrf.value = auth.csrf;
  const button = document.createElement("button");
  button.type = "submit";
  button.className = "logout-button";
  button.textContent = "Вийти (" + auth.username + ")";
  form.append(csrf, button);
  actions.appendChild(form);
}
function activeRoute() {
  const name = location.hash.replace(/^#\/?/, "");
  return views.some(v => v[0] === name) ? name : "overview";
}
function buildNav() {
  clear(navigation);
  const sections = [
    ["МОНІТОРИНГ", ["overview", "parallel", "pv", "battery", "grid", "load", "inverter"]],
    ["АНАЛІТИКА", ["trends", "history", "archive", "events"]],
    ["НАЛАШТУВАННЯ", ["modbus", "settings", "system"]]
  ];
  for (const [label, names] of sections) {
    navigation.appendChild(element("div", "nav-group-label", label));
    for (const name of names) {
      const view = views.find(v => v[0] === name);
      const item = element("a", state.route === name ? "active" : "");
      item.href = "#/" + name;
      if (state.route === name) item.setAttribute("aria-current", "page");
      item.title = view[1];
      add(item, element("span", "symbol", view[2]), element("span", "", view[1]));
      navigation.appendChild(item);
    }
  }
}
function updateConnection(connected) {
  const node = document.getElementById("connection");
  node.className = "connection " + (connected ? "ok" : "error");
  node.replaceChildren();
  add(node, element("i", ""), document.createTextNode(connected ? "MySQL доступна" : "MySQL недоступна"));
  document.getElementById("updated").textContent = "З’єднання перевірено: " + time(new Date().toISOString());
}
function planCount() {
  return state.catalog ? state.catalog.all_metric_count : 0;
}
function groupItems(groups) {
  if (!state.catalog) return [];
  const items = [];
  for (const group of groups) {
    for (const item of (state.catalog.groups[group] || [])) items.push(item);
  }
  return items;
}
function verifiedKeys() {
  return new Set(state.verified.map(s => s.signal_key));
}
function addStatusMessage() {
    if (!state.status) {
    message("Підключення до MySQL не підтверджено",
      "Перевірте окремий read-only обліковий запис і локальний сервіс Rakurs. Значення не підставляються.", true);
  } else if (!state.status.has_real_samples) {
    message("Очікування реальних вимірювань Deye",
      "Структура бази rakurs готова, але даних із підтвердженого пристрою ще немає. «—» означає відсутність даних, не нуль.");
  }
}
function reading(values, key) {
  const item = values[key];
  return item && item.value_decimal !== null && item.value_decimal !== undefined ?
    Number(item.value_decimal) : null;
}
function pretty(value, decimals = 1) {
  return value === null || !Number.isFinite(value) ? "—" :
    value.toLocaleString("uk-UA", {minimumFractionDigits: 0, maximumFractionDigits: decimals});
}
function metricCard(label, icon, key, values, tone = "neutral", subtitle = "") {
  const data = values[key];
  const value = reading(values, key);
  const card = element("article", "card kpi kpi-" + tone);
  const head = element("div", "kpi-head");
  const observed = data && data.observed_at_utc;
  const age = observed ? (Date.now()-new Date(observed).getTime())/1000 : null;
  const fresh = age !== null && Number.isFinite(age) && age >= -60 && age <= 120;
  add(head, element("span", "kpi-icon", icon), element("span", "kpi-state",
    value === null ? "Немає даних" : fresh ? "Свіжий запис" : "Архівний запис"));
  card.appendChild(head);
  card.appendChild(element("h3", "", label));
  const numberRow = element("div", "kpi-reading");
  add(numberRow, element("strong", "", pretty(value)),
    element("span", "unit", value !== null ? (data.unit || "") : ""));
  card.appendChild(numberRow);
  if (key === "battery_soc" && value !== null) {
    const meter = element("progress", "soc-meter");
    meter.setAttribute("aria-label", "Рівень заряду акумулятора");
    meter.max = 100;
    meter.value = Math.min(100, Math.max(0, value));
    card.appendChild(meter);
  }
  const description = key === "grid_power" && value !== null ?
    value > 0 ? "Імпорт з мережі" : value < 0 ? "Експорт у мережу" : "Обмін із мережею: 0" :
    key === "battery_soc" ? "Рівень заряду накопичувача" :
    subtitle;
  card.appendChild(element("p", "kpi-caption", description));
  if (data && data.observed_at_utc) {
    card.appendChild(element("span", "kpi-time", "Вимір: " + time(data.observed_at_utc)));
  }
  return card;
}
function flowNode(label, icon, value, unit, tone, detail) {
  const node = element("div", "energy-node energy-" + tone);
  add(node, element("span", "energy-icon", icon),
    element("span", "energy-name", label),
    element("strong", "energy-value", pretty(value) + (value === null ? "" : " " + unit)),
    element("span", "energy-caption", detail));
  return node;
}
function energyFlow(values) {
  const root = panel("Як рухається енергія", "Поточний баланс потужності · значення з MySQL");
  root.className += " energy-panel";
  const pv = reading(values, "pv_power");
  const load = reading(values, "load_power");
  const battery = reading(values, "battery_power");
  const grid = reading(values, "grid_power");
  if ([pv, load, battery, grid].some(v => v === null)) {
    root.appendChild(createEmpty("Поки що немає повного балансу",
      "Для схеми потрібні потужності сонця, навантаження, акумулятора і мережі."));
    return root;
  }
  const diagram = element("div", "energy-diagram");
  const center = element("div", "energy-hub");
  add(center, element("span", "hub-symbol", "↗"),
    element("strong", "", "ІНВЕРТОР"),
    element("small", "", "Енергетичний вузол"));
  const solar = flowNode("Сонце", "☀", pv, "кВт", "solar", "Генерує");
  const accumulator = flowNode("Акумулятор", "▣", Math.abs(battery), "кВт",
    "battery", battery > 0 ? "Розряджається → система" :
               battery < 0 ? "Заряджається ← система" : "Без обміну");
  const utility = flowNode("Мережа", "⌁", Math.abs(grid), "кВт",
    "grid", grid > 0 ? "Імпорт → система" :
            grid < 0 ? "Експорт ← система" : "Обміну немає");
  const consumer = flowNode("Споживання", "⌂", load, "кВт",
    "load", "Навантаження будинку");
  const arrow = (label, direction, tone) => {
    const link = element("div", "flow-link flow-" + tone);
    add(link, element("span", "flow-label", label),
      element("span", "flow-arrow", direction));
    return link;
  };
  add(diagram, solar, arrow("Генерація", "→", "solar"), center,
    arrow("Споживання", "→", "load"), consumer,
    utility, arrow(grid >= 0 ? "Імпорт" : "Експорт", grid >= 0 ? "→" : "←", "grid"),
    element("div", "energy-spacer"),
    arrow(battery >= 0 ? "Розряд" : "Заряд", battery >= 0 ? "←" : "→", "battery"),
    accumulator);
  root.appendChild(diagram);
  const note = element("p", "energy-note",
    "Напрямки відповідають знаку потужності у збережених даних. " +
    "Схема показує збережені показники одного пристрою, не суму двох інверторів.");
  root.appendChild(note);
  return root;
}
function detailMetric(label, key, values, icon = "◦") {
  const record = values[key];
  const cell = element("div", "detail-tile");
  const header = element("div", "detail-head");
  add(header, element("span", "", icon), element("span", "", label));
  cell.appendChild(header);
  const value = reading(values, key);
  const val = element("div", "detail-value");
  add(val, element("strong", "", pretty(value)), element("small", "",
    value !== null && record ? (record.unit || "") : ""));
  cell.appendChild(val);
  return cell;
}
function panel(title, description) {
  const p = element("section", "panel");
  if (title) p.appendChild(element("h2", "", title));
  if (description) p.appendChild(element("p", "", description));
  return p;
}
function current(cycle) { return cycle === state.cycle; }
async function renderOverview(cycle, primedValues = null) {
  addStatusMessage();
  const keys = ["pv_power", "load_power", "grid_power", "battery_soc",
    "battery_power", "inverter_temperature", "pv_energy_today", "load_energy_today"];
  let values = primedValues;
  if (!values) {
    try {
      values = (await api("/latest?keys=" + keys.join(","))).values || {};
    } catch (_error) {
      if (current(cycle)) message("Дані тимчасово недоступні",
        "Останні відомі значення залишаються на сервері.", true);
      return;
    }
  }
  if (!current(cycle)) return;

  const intro = element("div", "dashboard-intro");
  add(intro, element("div", "eyebrow", "ЕНЕРГОСИСТЕМА · ОГЛЯД"),
    element("h2", "", "Енергія під контролем"),
    element("p", "", "Основні показники, напрямки потоків та зміни за часом — на одному екрані."));
  main.appendChild(intro);
  await renderTwinSummary(cycle);
  if (!current(cycle)) return;
  section("Останні показники · Deye №" + state.selectedSlotNo,
    "Дані обраного інвертора MySQL · без сумування двох пристроїв");
  const deck = element("div", "cards cards-main");
  const batteryPower = reading(values, "battery_power");
  deck.appendChild(metricCard("Генерація сонця", "☀", "pv_power", values, "solar",
    "Потужність фотоелектричних панелей"));
  deck.appendChild(metricCard("Навантаження", "⌂", "load_power", values, "load",
    "Поточне споживання"));
  deck.appendChild(metricCard("Заряд акумулятора", "▣", "battery_soc", values, "battery"));
  const grid = metricCard("Мережа", "⌁", "grid_power", values, "grid");
  deck.appendChild(grid);
  main.appendChild(deck);

  section("Енергетична схема", "Зрозумілий напрямок потужності");
  main.appendChild(energyFlow(values));

  section("За сьогодні", "Додаткові показники з MySQL");
  const extras = element("div", "details-grid");
  extras.appendChild(detailMetric("Вироблено сонцем", "pv_energy_today", values, "☀"));
  extras.appendChild(detailMetric("Спожито", "load_energy_today", values, "⌂"));
  extras.appendChild(detailMetric("Потужність АКБ", "battery_power", values,
    batteryPower !== null && batteryPower < 0 ? "↙" : "↗"));
  extras.appendChild(detailMetric("Температура інвертора", "inverter_temperature", values, "♨"));
  main.appendChild(extras);

  await renderTrendDeck(cycle, 24, true);
  if (!current(cycle)) return;
  const footer = element("div", "overview-footer");
  const statusText = state.status ?
    (state.status.displayed_signals || state.status.simulated_signals ||
      state.status.verified_signals || 0) + " показників у базі" :
    "Джерело даних: MySQL";
  add(footer, element("span", "", statusText),
    element("span", "", "Збережені показники MySQL"));
  main.appendChild(footer);
}
async function renderTrendDeck(cycle, initialHours = 24, compact = false) {
  const root = element("section", compact ? "trend-section" : "trend-section full-trends");
  const heading = element("div", "trend-section-head");
  const title = element("div", "");
  add(title, element("h2", "", compact ? "Динаміка показників" : "Графіки енергосистеми"),
    element("p", "", "Історичні записи MySQL · усі години за Києвом"));
  heading.appendChild(title);
  const ranges = element("div", "range-control");
  ranges.setAttribute("role", "group");
  ranges.setAttribute("aria-label", "Період графіків");
  const frames = [[24, "24 год"], [72, "3 доби"], [168, "7 діб"]];
  let hours = initialHours;
  const buttons = frames.map(([value, label]) => {
    const btn = element("button", value === hours ? "active" : "", label);
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(value === hours));
    btn.addEventListener("click", () => {
      hours = value;
      buttons.forEach((other, i) => {
        other.className = frames[i][0] === hours ? "active" : "";
        other.setAttribute("aria-pressed", String(frames[i][0] === hours));
      });
      refresh();
    });
    ranges.appendChild(btn);
    return btn;
  });
  heading.appendChild(element("span","subtle","Deye №"+state.selectedSlotNo));
  heading.appendChild(ranges);
  root.appendChild(heading);
  const grid = element("div", "trend-grid");
  root.appendChild(grid);
  const series = [
    ["pv_power", "Сонячна генерація", "кВт", "solar"],
    ["load_power", "Навантаження", "кВт", "load"],
    ["battery_soc", "Заряд акумулятора", "%", "battery"],
    ["grid_power", "Обмін із мережею", "кВт", "grid"]
  ];
  const bodies = series.map(([key, label, unit, tone]) => {
    const chartCard = element("article", "trend-card trend-" + tone);
    const cap = element("div", "trend-card-caption");
    add(cap, element("h3", "", label), element("span", "", unit));
    chartCard.appendChild(cap);
    const container = element("div", "trend-content");
    chartCard.appendChild(container);
    grid.appendChild(chartCard);
    return container;
  });
  main.appendChild(root);
  let requestIndex = 0;
  async function refresh() {
    const index = ++requestIndex;
    bodies.forEach(body => body.replaceChildren(element("p", "subtle", "Завантаження графіка…")));
    const results = await Promise.allSettled(series.map(([key]) =>
      api("/history?key=" + encodeURIComponent(key) + "&hours=" + hours)));
    if (!current(cycle) || index !== requestIndex) return;
    results.forEach((result, i) => {
      const body = bodies[i];
      clear(body);
      if (result.status !== "fulfilled") {
        body.appendChild(createEmpty("Дані недоступні", "Не вдалося прочитати історію MySQL."));
        return;
      }
      const points = result.value.points || [];
      const numeric = points.filter(p => p.value_decimal !== null);
      if (!numeric.length) {
        body.appendChild(createEmpty("Немає вимірювань", "За вибраний період записів немає."));
        return;
      }
      const currentValue = numeric[numeric.length - 1].value_decimal;
      body.appendChild(element("strong", "trend-latest", pretty(Number(currentValue)) +
        " " + series[i][2]));
      body.appendChild(svgChart(points, series[i][2]));
      body.appendChild(element("p", "subtle", points.length +
        " точок · " + time(points[0].observed_at_utc) +
        " — " + time(points[points.length - 1].observed_at_utc)));
    });
  }
  await refresh();
}
async function renderTrends(cycle) {
  addStatusMessage();
  await renderTrendDeck(cycle, 72, false);
}

async function renderGroup(groups, title, cycle) {
  addStatusMessage();
  const items = groupItems(groups);
  section(title + " · Deye №" + state.selectedSlotNo,
    items.length + " параметрів · останні збережені показники");
  if (!items.length) {
    main.appendChild(createEmpty("Каталог недоступний", "Не вдалося прочитати реєстр."));
    return;
  }
  const known = verifiedKeys();
  const keys = items.filter(i => known.has(i.key)).map(i => i.key);
  const values = {};
  for (let i = 0; i < keys.length; i += 24) {
    try {
      const chunk = keys.slice(i, i + 24);
      Object.assign(values, (await api("/latest?keys=" + chunk.join(","))).values || {});
    } catch (_error) {
      if (current(cycle)) message("Частина показників недоступна",
        "Останні значення з MySQL не вдалося отримати повністю.", true);
      break;
    }
  }
  if (!current(cycle)) return;
  const controls = element("div", "catalog-controls");
  const search = element("input", "search-field");
  search.type = "search";
  search.setAttribute("aria-label", "Пошук показника");
  search.placeholder = "Знайти показник за назвою або ключем…";
  const count = element("span", "catalog-count");
  controls.appendChild(search);
  controls.appendChild(count);
  main.appendChild(controls);
  const list = element("div", "metric-list");
  const entries = [];
  for (const metric of items) {
    const record = values[metric.key];
    const hasValue = record && record.value_decimal !== null;
    const row = element("article", "metric");
    const info = element("div", "metric-info");
    add(info, element("strong", "", metric.label),
      element("small", "", metric.key));
    const val = element("div", "metric-reading");
    add(val, element("span", "number" + (hasValue ? "" : " unknown"),
        hasValue ? pretty(Number(record.value_decimal), 3) : "—"),
      element("small", "", hasValue ? record.unit || "" : "Немає значення"));
    row.title = hasValue ? "Запис MySQL · " + time(record.observed_at_utc) :
      "Параметр поки не має збереженого вимірювання";
    add(row, info, val);
    entries.push({row, text: (metric.label + " " + metric.key).toLocaleLowerCase("uk")});
    list.appendChild(row);
  }
  main.appendChild(list);
  function filter() {
    const q = search.value.trim().toLocaleLowerCase("uk");
    let visible = 0;
    for (const entry of entries) {
      const show = entry.text.includes(q);
      entry.row.hidden = !show;
      if (show) visible++;
    }
    count.textContent = "Показано " + visible + " із " + items.length;
  }
  search.addEventListener("input", filter);
  filter();
}
function svgChart(points, unit) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", "0 0 900 280");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Графік вимірювань з MySQL");
  const real = points.map((p, index) => ({index, v: Number(p.value_decimal)}))
    .filter(p => points[p.index].value_decimal !== null && Number.isFinite(p.v));
  if (!real.length) return createEmpty("Графік без точок", "За вибраний період числових значень у MySQL немає.");
  const min = Math.min(...real.map(p => p.v));
  const max = Math.max(...real.map(p => p.v));
  const span = Math.max(max - min, Math.abs(max) * .08, .1);
  const low = min - span * .15, high = max + span * .15;
  for (let i = 0; i < 4; i++) {
    const line = document.createElementNS(svg.namespaceURI, "line");
    const y = 40 + i * 66;
    line.setAttribute("x1", "52"); line.setAttribute("x2", "868");
    line.setAttribute("y1", String(y)); line.setAttribute("y2", String(y));
    svg.appendChild(line);
    const label = document.createElementNS(svg.namespaceURI, "text");
    label.setAttribute("x", "7"); label.setAttribute("y", String(y + 3));
    label.textContent = ((high - (high - low) * i / 3).toFixed(2)) + (unit ? " " + unit : "");
    svg.appendChild(label);
  }
  if (real.length === 1) {
    const y = 238 - (real[0].v - low)*198/(high-low);
    const dot = document.createElementNS(svg.namespaceURI,"circle");
    dot.setAttribute("cx","460");dot.setAttribute("cy",String(y));
    dot.setAttribute("r","6");dot.setAttribute("fill","#71e6d8");
    svg.appendChild(dot);
    const label=document.createElementNS(svg.namespaceURI,"text");
    label.setAttribute("x","460");label.setAttribute("y",String(y-16));
    label.setAttribute("text-anchor","middle");
    label.textContent=pretty(real[0].v,3)+" "+(unit||"");
    svg.appendChild(label);
  }
  const polyline = document.createElementNS(svg.namespaceURI, "polyline");
  const pointsString = real.map(p => {
    const x = real.length === 1 ? 460 : 52 + p.index * 816 / Math.max(1, points.length - 1);
    const y = 238 - (p.v - low) * 198 / (high - low);
    return x.toFixed(2) + "," + y.toFixed(2);
  }).join(" ");
  polyline.setAttribute("points", pointsString);
  if (real.length > 1) svg.appendChild(polyline);
  const first = points[real[0].index];
  const last = points[real[real.length - 1].index];
  if (first && last) {
    const labels = real.length === 1 ? [[460,first.observed_at_utc,"middle"]] : [
      [52, first.observed_at_utc, "start"],
      [868, last.observed_at_utc, "end"]
    ];
    for (const [x, observed, anchor] of labels) {
      const node = document.createElementNS(svg.namespaceURI, "text");
      node.setAttribute("x", String(x));
      node.setAttribute("y", "264");
      node.setAttribute("text-anchor", anchor);
      const date = new Date(observed);
      node.textContent = Number.isNaN(date.getTime()) ? "" :
        date.toLocaleString("uk-UA", {
          timeZone: "Europe/Kyiv", day: "2-digit", month: "2-digit",
          hour: "2-digit", minute: "2-digit"
        });
      svg.appendChild(node);
    }
  }
  return svg;
}
async function renderHistory(cycle, archived) {
  addStatusMessage();
  section((archived ? "Архів вимірювань" : "Історія показників") +
    " · Deye №" + state.selectedSlotNo,
    "Графік і журнал обраного інвертора · час Київ");
  const available = state.verified;
  if (!available.length) {
    main.appendChild(createEmpty("Немає збережених сигналів",
      "База не повернула перелік доступних параметрів."));
    return;
  }
  const panelRoot = element("section", "history-workspace");
  const controls = element("div", "history-controls");
  const signalField = element("label", "field-label");
  signalField.appendChild(element("span", "", "Показник"));
  const picker = element("select", "");
  picker.setAttribute("aria-label", "Показник для історії");
  for (const sig of available) {
    const option = element("option", "", sig.label_uk + " · " + sig.signal_key);
    option.value = sig.signal_key;
    if (sig.signal_key === "pv_power") option.selected = true;
    picker.appendChild(option);
  }
  signalField.appendChild(picker);
  const timeField = element("label", "field-label");
  timeField.appendChild(element("span", "", "Період"));
  const range = element("select", "");
  range.setAttribute("aria-label", "Період історії");
  for (const [hours, label] of [[1, "Остання година"], [6, "6 годин"],
    [12, "12 годин"], [24, "24 години"], [72, "3 доби"], [168, "7 діб"]]) {
    const option = element("option", "", label);
    option.value = String(hours);
    if (hours === 72) option.selected = true;
    range.appendChild(option);
  }
  timeField.appendChild(range);
  controls.appendChild(signalField);
  controls.appendChild(timeField);
  panelRoot.appendChild(controls);
  const plot = element("div", "history-chart");
  panelRoot.appendChild(plot);
  const tableArea = element("div", "history-records");
  panelRoot.appendChild(tableArea);
  main.appendChild(panelRoot);
  let requestId = 0;
  async function refresh() {
    const id = ++requestId;
    plot.replaceChildren(element("p", "subtle", "Завантаження історії…"));
    clear(tableArea);
    try {
      const result = await api("/history?key=" +
        encodeURIComponent(picker.value) + "&hours=" + range.value);
      if (!current(cycle) || id !== requestId) return;
      clear(plot);
      const points = result.points || [];
      if (!points.length) {
        plot.appendChild(createEmpty("За цей період записів немає",
          "Вибери інший сигнал або довший проміжок часу."));
        return;
      }
      const unit = result.signal && result.signal.unit || "";
      const stats = element("div", "history-summary");
      const numericPoints = points.filter(point => point.value_decimal !== null);
      const latestNumeric = numericPoints.length ? numericPoints[numericPoints.length - 1] : null;
      add(stats, element("strong", "",
        latestNumeric ? pretty(Number(latestNumeric.value_decimal), 3) + " " + unit : "—"),
        element("span", "", points.length + " записів · " +
          time(points[0].observed_at_utc) + " — " +
          time(points[points.length - 1].observed_at_utc)));
      plot.appendChild(stats);
      plot.appendChild(svgChart(points, unit));

      const head = element("div", "history-table-head");
      head.appendChild(element("h3", "", "Останні вимірювання"));
      head.appendChild(element("span", "subtle", "Час Київ · " +
        (DEMO_MODE ? "архів MySQL" : "MySQL")));
      tableArea.appendChild(head);
      const wrapper = element("div", "table-wrap");
      const table = element("table", "data-table");
      const thead = element("thead", "");
      const hr = element("tr", "");
      ["Дата й час", "Значення", "Джерело"].forEach(t =>
        hr.appendChild(element("th", "", t)));
      thead.appendChild(hr);
      const tbody = element("tbody", "");
      for (const entry of points.slice(-30).reverse()) {
        const tr = element("tr", "");
        [time(entry.observed_at_utc),
         entry.value_decimal === null ? "—" : pretty(Number(entry.value_decimal), 3) + " " + unit,
         "MySQL"
        ].forEach(value => tr.appendChild(element("td", "", value)));
        tbody.appendChild(tr);
      }
      add(table, thead, tbody);
      wrapper.appendChild(table);
      tableArea.appendChild(wrapper);
    } catch (_error) {
      if (current(cycle) && id === requestId) {
        clear(plot);
        plot.appendChild(createEmpty("Не вдалося отримати історію",
          "Перевірте з'єднання з MySQL і спробуйте знову."));
      }
    }
  }
  picker.addEventListener("change", refresh);
  range.addEventListener("change", refresh);
  await refresh();
}
function displayDeviceKey(value) {
  const key = String(value || "");
  if (key === "simulated_deye_sg05lp3") return "Deye · основний профіль";
  if (key === "sim_deye20k_parallel_1") return "Deye №1";
  if (key === "sim_deye20k_parallel_2") return "Deye №2";
  return key || "—";
}
function deviceLabel(device, index = 0) {
  const slot = Number(device.slot_no || index + 1);
  return "Deye №" + (Number.isInteger(slot) && slot > 0 ? slot : index + 1);
}
function deviceReading(values, key) {
  const item = values?.[key];
  return item && item.value_decimal !== null && item.value_decimal !== undefined ?
    number(item.value_decimal) + " " + (item.unit || "") : "—";
}
function storedDeviceNote(reading) {
  return reading?.observed_at_utc ?
    "Останній запис: " + time(reading.observed_at_utc) : "Записів немає";
}
async function fetchParallelDevices(cycle) {
  const profile = await api("/parallel");
  if (!current(cycle)) return null;
  const devices = Array.isArray(profile.devices) ? profile.devices : [];
  const responses = await Promise.allSettled(devices.map(d =>
    api("/parallel/latest?device=" + encodeURIComponent(d.device_key))));
  if (!current(cycle)) return null;
  return devices.map((d, i) => ({
    ...d, slot_no: Number(d.slot_no || i + 1),
    values: responses[i].status === "fulfilled" ? (responses[i].value.values || {}) : {},
  }));
}
async function renderTwinSummary(cycle) {
  let devices;
  try { devices = await fetchParallelDevices(cycle); } catch (_error) { return; }
  if (!current(cycle) || !devices?.length) return;
  if (state.selectedDeviceKey) {
    const selected = devices.find(d => d.device_key === state.selectedDeviceKey);
    if (selected) state.selectedSlotNo = selected.slot_no;
  }
  section("Два інвертори Deye", "Показники кожного інвертора з MySQL");
  const grid = element("div", "parallel-home-grid");
  main.appendChild(grid);
  for (const [i, device] of devices.entries()) {
    const card = element("article", "parallel-device parallel-home-device");
    const head = element("div", "parallel-device-head");
    add(head, element("strong", "parallel-device-name", deviceLabel(device, i)),
      element("span", "parallel-state", "MYSQL"));
    card.appendChild(head);
    card.appendChild(element("p", "parallel-device-model",
      device.model || "SUN-20K-SG05LP3-EU-SM2"));
    const metrics = element("div", "parallel-metric-list");
    for (const [key, label] of [
      ["pv_power", "PV"], ["battery_power", "АКБ"], ["battery_soc", "SOC"]
    ]) {
      const tile = element("div", "parallel-reading");
      add(tile, element("small", "", label),
        element("strong", "", deviceReading(device.values, key)),
        element("span", "", storedDeviceNote(device.values[key])));
      metrics.appendChild(tile);
    }
    card.appendChild(metrics);
    const select = element("button", "parallel-open-link",
      "Показати Deye №" + device.slot_no + " на головній");
    select.type = "button";
    select.setAttribute("aria-pressed", String(
      state.selectedDeviceKey === device.device_key ||
      (!state.selectedDeviceKey && device.slot_no === 1)));
    select.addEventListener("click", () => {
      if (state.selectedDeviceKey === device.device_key) return;
      state.selectedDeviceKey = device.device_key;
      state.selectedSlotNo = device.slot_no;
      try { if (typeof localStorage !== "undefined")
        localStorage.setItem("rakurs.selectedDeye",device.device_key);
      } catch (_error) {}
      render();
    });
    card.appendChild(select);
    const link = element("a", "parallel-open-link", "Усі показники інверторів");
    link.href = "#/parallel";
    card.appendChild(link);
    grid.appendChild(card);
  }
}
async function renderParallel(cycle) {
  addStatusMessage();
  section("Паралельна система Deye",
    "SUN-20K-SG05LP3-EU-SM2 · два інвертори · спільна АКБ");
  let devices;
  try { devices = await fetchParallelDevices(cycle); }
  catch (_error) {
    main.appendChild(createEmpty("Не вдалося прочитати інвертори",
      "Перевірте MySQL і повторіть запит."));
    return;
  }
  if (!current(cycle)) return;
  if (!devices?.length) {
    main.appendChild(createEmpty("Інверторів у базі поки немає",
      "Після очищення бази дані з'являться після першого завантаження."));
    return;
  }
  const grid = element("div", "parallel-devices");
  main.appendChild(grid);
  for (const [i, device] of devices.entries()) {
    const query = "?device=" + encodeURIComponent(device.device_key);
    const card = element("article", "parallel-device");
    const heading = element("div", "parallel-device-head");
    add(heading, element("strong", "parallel-device-name", deviceLabel(device, i)),
      element("span", "parallel-state", "MYSQL"));
    card.appendChild(heading);
    card.appendChild(element("p", "parallel-device-model",
      device.model || "SUN-20K-SG05LP3-EU-SM2"));
    const metrics = element("div", "parallel-metric-list");
    for (const [key, label] of [
      ["pv_power", "Генерація PV"], ["battery_power", "Потужність АКБ"],
      ["battery_soc", "SOC"], ["load_power", "Навантаження"],
      ["grid_power", "Мережа"], ["inverter_temperature", "Температура"]
    ]) {
      const tile = element("div", "parallel-reading");
      add(tile, element("small", "", label),
        element("strong", "", deviceReading(device.values, key)),
        element("span", "", storedDeviceNote(device.values[key])));
      metrics.appendChild(tile);
    }
    card.appendChild(metrics);
    grid.appendChild(card);
    const result = await Promise.allSettled([
      api("/parallel/history" + query + "&key=pv_power&hours=24"),
      api("/parallel/modbus-fields" + query),
      api("/parallel/raw" + query + "&limit=200"),
      api("/parallel/events" + query),
    ]);
    if (!current(cycle)) return;
    const [history, fields, raw, events] = result;
    const fieldList = fields.status === "fulfilled" ?
      (fields.value.fields || []) : [];
    const rawRows = raw.status === "fulfilled" ?
      (raw.value.raw_registers || []) : [];
    const info = element("div", "parallel-demo-counters");
    for (const [label, count] of [
      ["Поля Modbus", fieldList.length],
      ["Сирі регістри", raw.status === "fulfilled" ? raw.value.total : 0],
      ["Події", events.status === "fulfilled" ? (events.value.events || []).length : 0]
    ]) {
      const item = element("div", "parallel-demo-counter");
      add(item, element("strong", "", String(count || 0)),
        element("small", "", label));
      info.appendChild(item);
    }
    card.appendChild(info);
    const chart = element("section", "parallel-device-chart");
    chart.appendChild(element("h3", "", "Генерація PV · останні 24 години"));
    const points = history.status === "fulfilled" ? (history.value.points || []) : [];
    if (points.filter(p => p.value_decimal !== null).length === 0) {
      chart.appendChild(createEmpty("Вимірювань немає",
        "За останні 24 години немає числових значень."));
    } else {
      chart.appendChild(svgChart(points, "кВт"));
    }
    card.appendChild(chart);

    const rawPanel = element("section", "parallel-raw-viewer");
    rawPanel.appendChild(element("h3", "", "Регістри Modbus"));
    const rawArea = element("div", "parallel-raw-table");
    rawPanel.appendChild(rawArea);
    const rawMore = element("button", "more-button", "Показати регістри");
    rawMore.type = "button";
    rawPanel.appendChild(rawMore);
    card.appendChild(rawPanel);
    let rawOffset = 0;
    let rowsCache = rawRows;
    const totalRaw = raw.status === "fulfilled" ? Number(raw.value.total || 0) : 0;
    const pollUid = raw.status === "fulfilled" ? (raw.value.poll?.poll_uid || "") : "";
    async function loadRaw() {
      rawMore.disabled = true;
      try {
        const data = rawOffset === 0 && rowsCache ? rowsCache :
          (await api("/parallel/raw" + query +
            "&limit=200&offset=" + rawOffset +
            (pollUid ? "&poll_uid=" + encodeURIComponent(pollUid) : ""))).raw_registers || [];
        rowsCache = null;
        if (!current(cycle)) return;
        for (const entry of data) {
          const row = element("div", "parallel-raw-row");
          add(row, element("code", "", String(entry.register_address)),
            element("span", "", "FC" + entry.function_code),
            element("strong", "", String(entry.raw_uint16)));
          rawArea.appendChild(row);
        }
        rawOffset += data.length;
        rawMore.textContent = "Показано " + rawOffset + " із " + totalRaw +
          (rawOffset < totalRaw ? " · ще 200" : "");
        rawMore.hidden = rawOffset >= totalRaw || data.length === 0;
      } catch (_error) {
        rawArea.appendChild(createEmpty("Регістри недоступні",
          "Не вдалося прочитати записи MySQL."));
      } finally {
        if (current(cycle)) rawMore.disabled = false;
      }
    }
    rawMore.addEventListener("click", loadRaw);
    if (!totalRaw) {
      rawMore.hidden = true;
      rawArea.appendChild(createEmpty("Регістри відсутні",
        "Поки немає збережених регістрів для цього інвертора."));
    }

    const fieldPanel = element("section", "parallel-raw-viewer parallel-field-viewer");
    fieldPanel.appendChild(element("h3", "",
      "Параметри Modbus — " + fieldList.length));
    const fieldArea = element("div", "parallel-raw-table");
    fieldPanel.appendChild(fieldArea);
    const fieldMore = element("button", "more-button", "Показати параметри");
    fieldMore.type = "button";
    fieldPanel.appendChild(fieldMore);
    card.appendChild(fieldPanel);
    let fieldOffset = 0;
    function loadFields() {
      for (const entry of fieldList.slice(fieldOffset, fieldOffset + 80)) {
        const row = element("div", "parallel-field-row");
        const shown = entry.value_decimal !== null &&
          entry.value_decimal !== undefined ? number(entry.value_decimal) :
          (entry.value_text || "—");
        add(row, element("code", "", entry.field_key),
          element("span", "", entry.field_name),
          element("strong", "", shown + " " + (entry.unit || "")));
        fieldArea.appendChild(row);
      }
      fieldOffset += Math.min(80, fieldList.length - fieldOffset);
      fieldMore.textContent = "Показано " + fieldOffset + " із " + fieldList.length;
      fieldMore.hidden = fieldOffset >= fieldList.length;
    }
    fieldMore.addEventListener("click", loadFields);
    if (!fieldList.length) {
      fieldMore.hidden = true;
      fieldArea.appendChild(createEmpty("Поля Modbus відсутні",
        "Для цього інвертора немає записів каталогу."));
    }
  }
}

async function renderEvents(cycle) {
  section("Журнал подій", "Події обладнання та підтверджені операції імпорту MySQL");
  const summary = element("div", "event-summary");
  const deviceCard = element("div", "event-count-card");
  const batchCard = element("div", "event-count-card");
  add(deviceCard, element("strong", "", "…"),
    element("span", "", "Події обладнання"));
  add(batchCard, element("strong", "", "…"),
    element("span", "", "Операції завантаження"));
  add(summary, deviceCard, batchCard);
  main.appendChild(summary);

  const eventPanel = panel("Події обладнання",
    "Фактичні записи журналу пристрою. Звичайні вимірювання не вважаються аваріями.");
  const deviceBody = element("div", "events-list");
  eventPanel.appendChild(deviceBody);
  main.appendChild(eventPanel);

  const batchPanel = panel("Історія завантажень",
    "Системні записи з rakurs_ingest_batches. Це не аварії Deye.");
  const batchBody = element("div", "events-list");
  batchPanel.appendChild(batchBody);
  main.appendChild(batchPanel);

  const responses = await Promise.allSettled([api("/events"), api("/batches")]);
  if (!current(cycle)) return;
  const [events, batches] = responses;
  if (events.status !== "fulfilled") {
    deviceCard.childNodes[0].textContent = "—";
    deviceBody.appendChild(createEmpty("Журнал недоступний",
      "Не вдалося прочитати rakurs_device_events."));
  } else {
    const items = events.value.events || [];
    deviceCard.childNodes[0].textContent = String(items.length);
    if (!items.length) {
      deviceBody.appendChild(createEmpty("Подій обладнання поки немає",
        "Для цього пристрою у MySQL немає зафіксованих повідомлень чи аварій."));
    }
    for (const record of items) {
      const row = element("article", "event-row");
      const level = String(record.severity || "info").toLowerCase();
      row.className += " event-" + (/^(error|critical|alarm)$/.test(level) ?
        "critical" : /^(warning|warn)$/.test(level) ? "warning" : "info");
      const marker = element("span", "event-mark", "●");
      const info = element("div", "event-description");
      add(info, element("strong", "", record.message || record.event_code || "Подія"),
        element("small", "", (record.event_code || "Без коду") +
          " · " + displayDeviceKey(record.device_key)));
      const stamp = element("div", "event-stamp");
      add(stamp, element("span", "event-severity", level),
        element("time", "", time(record.observed_at_utc)));
      add(row, marker, info, stamp);
      deviceBody.appendChild(row);
    }
  }
  if (batches.status !== "fulfilled") {
    batchCard.childNodes[0].textContent = "—";
    batchBody.appendChild(createEmpty("Журнал завантажень недоступний",
      "Не вдалося прочитати rakurs_ingest_batches."));
  } else {
    const items = batches.value.batches || [];
    batchCard.childNodes[0].textContent = String(items.length);
    if (!items.length) {
      batchBody.appendChild(createEmpty("Завантаження не зафіксовані",
        "У журналі імпорту поки немає записів."));
    }
    for (const batch of items) {
      const row = element("article", "event-row event-batch");
      const code = String(batch.error_code || "");
      const imported = batch.status === "simulated";
      const title = code === "DEYE_HISTORY_72H_V1" ?
        "Додано історію вимірювань за 72 години" :
        imported ? "Завантажено записи MySQL" :
        "Операція завантаження: " + (batch.status || "невідомо");
      const accepted = batch.accepted_count ?? 0;
      const received = batch.received_count ?? 0;
      const rejected = batch.rejected_count ?? 0;
      const marker = element("span", "event-mark", "↓");
      const info = element("div", "event-description");
      add(info, element("strong", "", title),
        element("small", "", "Прийнято: " + accepted +
          " · Отримано: " + received + " · Відхилено: " + rejected));
      const stamp = element("div", "event-stamp");
      add(stamp, element("span", "event-severity", "MySQL"),
        element("time", "", time(batch.finished_at_utc || batch.started_at_utc)));
      add(row, marker, info, stamp);
      batchBody.appendChild(row);
    }
  }
  const note = element("p", "events-note",
    "Відсутність записів у журналі подій не доводить, що аварій не було: " +
    "потрібне підключене джерело подій та його опитування.");
  main.appendChild(note);
}

async function renderTable(path, sectionName, headings, fields, cycle) {
  addStatusMessage();
  section(sectionName, DEMO_MODE ? "Архів MySQL" :
                                        "Записи виключно з MySQL rakurs");
  const root = panel(sectionName);
  main.appendChild(root);
  try {
    const payload = await api(path);
    if (!current(cycle)) return;
    const source = payload.events || payload.devices || payload.batches || [];
    if (!source.length) { root.appendChild(createEmpty("Записів поки немає", "Наявні записи відображаються без підстановки значень.")); return; }
    const wrap = element("div", "table-wrap"), table = element("table", "data-table");
    const thead = element("thead", ""), tr = element("tr", "");
    headings.forEach(t => tr.appendChild(element("th", "", t)));
    thead.appendChild(tr);
    const tbody = element("tbody", "");
    source.forEach(item => {
      const row = element("tr", "");
      fields.forEach(field => {
        const raw = item[field];
        const displayed = field.endsWith("_utc") ? time(raw) :
          field === "kind" && (raw === "simulation" || raw === "inverter") ? "Інвертор" :
          field === "device_key" ? displayDeviceKey(raw) :
          (raw ?? "—");
        row.appendChild(element("td", "", displayed));
      });
      tbody.appendChild(row);
    });
    add(table, thead, tbody); wrap.appendChild(table); root.appendChild(wrap);
  } catch (_error) { if (current(cycle)) root.appendChild(createEmpty("База недоступна", "Не вдалося прочитати записи.")); }
}
async function renderModbus(cycle) {
  addStatusMessage();
  section("Довідник Modbus · Deye №"+state.selectedSlotNo,
    "Технічні параметри · лише читання · значення MySQL");
  const root = panel("Регістр і значення",
    "Пошук за назвою, групою або адресою регістра");
  root.className += " modbus-workspace";
  main.appendChild(root);
  try {
    const response = await api("/modbus-fields");
    if (!current(cycle)) return;
    const fields = response.fields || [];
    root.appendChild(element("div", "modbus-stats",
      fields.length + " параметрів · " +
      (response.register_count || 0) + " адрес регістрів"));
    const toolbar = element("div", "modbus-toolbar");
    const search = element("input", "search-field");
    search.type = "search";
    search.placeholder = "Пошук за назвою, ключем, регістром…";
    search.setAttribute("aria-label", "Пошук Modbus");
    const group = element("select", "");
    group.setAttribute("aria-label", "Фільтр групи Modbus");
    const all = element("option", "", "Усі групи");
    all.value = "";
    group.appendChild(all);
    const groups = [...new Set(fields.map(f => f.group_name || "Інше"))].sort();
    for (const name of groups) {
      const option = element("option", "", name);
      option.value = name;
      group.appendChild(option);
    }
    toolbar.appendChild(search);
    toolbar.appendChild(group);
    root.appendChild(toolbar);
    const indicator = element("p", "subtle");
    root.appendChild(indicator);
    const wrapper = element("div", "table-wrap");
    const table = element("table", "data-table modbus-table");
    const thead = element("thead", ""), tr = element("tr", "");
    ["Група", "Параметр", "Регістр", "Значення", "Од."].forEach(t =>
      tr.appendChild(element("th", "", t)));
    thead.appendChild(tr);
    const tbody = element("tbody", "");
    add(table, thead, tbody);
    wrapper.appendChild(table);
    root.appendChild(wrapper);
    const more = element("button", "more-button", "Показати ще 100");
    more.type = "button";
    root.appendChild(more);
    const prepared = fields.map(f => {
      let addresses = f.registers_json || "[]";
      try {
        addresses = JSON.parse(addresses).map(n =>
          "0x" + Number(n).toString(16).toUpperCase().padStart(4, "0")).join(", ");
      } catch (_error) {
        addresses = String(addresses);
      }
      return {
        field: f, addresses,
        search: ((f.field_name || "") + " " + (f.field_key || "") + " " +
          (f.group_name || "") + " " + addresses).toLocaleLowerCase("uk")
      };
    });
    let matches = prepared, displayed = 0;
    function drawRows(start, end) {
      for (const item of matches.slice(start, end)) {
        const f = item.field;
        const row = element("tr", "");
        const value = f.value_text !== null && f.value_text !== undefined ?
          String(f.value_text) : f.value_decimal === null ?
          "—" : pretty(Number(f.value_decimal), 3);
        [f.group_name || "—", f.field_name || f.field_key,
         item.addresses || "Розрахунковий", value, f.unit || "—"].forEach(x =>
          row.appendChild(element("td", "", x)));
        tbody.appendChild(row);
      }
      displayed = end;
      indicator.textContent = "Показано " + displayed + " із " + matches.length;
      more.hidden = displayed >= matches.length;
    }
    function filter() {
      const q = search.value.trim().toLocaleLowerCase("uk");
      const selected = group.value;
      matches = prepared.filter(item => (!selected ||
        (item.field.group_name || "Інше") === selected) &&
        item.search.includes(q));
      displayed = 0;
      clear(tbody);
      drawRows(0, Math.min(100, matches.length));
    }
    search.addEventListener("input", filter);
    group.addEventListener("change", filter);
    more.addEventListener("click", () =>
      drawRows(displayed, Math.min(displayed + 100, matches.length)));
    filter();
  } catch (_error) {
    root.appendChild(createEmpty("Modbus недоступний",
      "Не вдалося отримати довідник регістрів MySQL."));
  }
}
async function renderSystem(cycle) {
  addStatusMessage();
  section("Конфігурація системи", "Показники та пристрої MySQL");
  const pane = panel("Єдина MySQL");
  const info = element("div", "kv");
  [
    ["Сервер", "192.168.5.30"],
    ["База", "rakurs"],
    ["Джерело", "MySQL"],
    ["Пристрої у базі", state.status ? state.status.registered_devices : "—"],
    ["Показники у базі", state.status ? state.status.displayed_signals : "—"],
    ["Автоматичне опитування", state.status?.collector_active ? "Увімкнено" : "Не запущено"]
  ].forEach(pair => {
    const item = element("div", "");
    add(item, element("strong", "", pair[1]), element("small", "", pair[0]));
    info.appendChild(item);
  });
  pane.appendChild(info);
  main.appendChild(pane);
  const spacer = element("div", "");
  main.appendChild(spacer);
  await renderTable("/devices", "Реєстр пристроїв", ["Ключ", "Тип", "Модель", "Зареєстровано"],
    ["device_key", "kind", "model", "commissioned_at_utc"], cycle);
}
async function render() {
  const cycle = ++state.cycle;
  state.route = activeRoute();
  buildNav();
  document.getElementById("view-title").textContent = views.find(v => v[0] === state.route)[1];
  let primedValues = null;
  if (state.route === "overview" && SSR_PRESENT && !state.selectedDeviceKey) {
    // This is the key guard: a failed/stale client API must not replace the
    // successful server-rendered database values with placeholders.
    const keys = ["pv_power", "load_power", "grid_power", "battery_soc",
      "battery_power", "inverter_temperature", "pv_energy_today", "load_energy_today"];
    try {
      const latest = await api("/latest?keys=" + encodeURIComponent(keys.join(",")));
      if (!current(cycle)) return;
      const values = latest.values || {};
      const critical = ["pv_power", "load_power", "battery_soc"];
      if (!critical.every(key => values[key] && values[key].value_decimal !== null)) {
        // Preserve server-rendered readings, and do not switch the page to —.
        restoreServerReadings();
        console.warn("Rakurs: keeping original MySQL readings; client API incomplete");
        // The independent twin archive is not dependent on the legacy single
        // inverter SSR values. Keep existing SSR and append twin cards only
        // when the two inverter data endpoints respond.
        if (!state.twinFallbackCycle || state.twinFallbackCycle !== cycle) {
          state.twinFallbackCycle = cycle;
          await renderTwinSummary(cycle);
        }
        return;
      }
      primedValues = values;
      // Database quality labels do not suppress existing values.
    } catch (_error) {
      if (current(cycle)) {
        restoreServerReadings();
        console.warn("Rakurs: keeping original MySQL readings; client API failed");
        state.twinFallbackCycle = cycle;
        await renderTwinSummary(cycle);
      }
      return;
    }
  }
  if (!current(cycle)) return;
  state.twinFallbackCycle = null;
  clear(main);
  switch (state.route) {
    case "overview": await renderOverview(cycle, primedValues); break;
    case "pv": await renderGroup(["pv"], "Сонячна генерація", cycle); break;
    case "battery": await renderGroup(
      ["battery", "bms"].concat(Array.from({length: 20}, (_, i) => "bms_module_" + (i + 1))),
      "Акумулятор і BMS", cycle); break;
    case "grid": await renderGroup(["grid"], "Електромережа", cycle); break;
    case "load": await renderGroup(["load"], "Навантаження", cycle); break;
    case "inverter": await renderGroup(["inverter", "output", "generator"], "Інвертор і генератор", cycle); break;
    case "parallel": await renderParallel(cycle); break;
    case "trends": await renderTrends(cycle); break;
    case "history": await renderHistory(cycle, false); break;
    case "events": await renderEvents(cycle); break;
    case "archive": await renderHistory(cycle, true); break;
    case "modbus": await renderModbus(cycle); break;
    case "system": await renderSystem(cycle); break;
    case "settings": await renderSettings(cycle); break;
  }
}
async function boot() {
  // Load the database status before rendering all views.
  const results = await Promise.allSettled([
    api("/catalog"), api("/status"), api("/signals"), api("/auth"), api("/parallel")
  ]);
  state.catalog = results[0].status === "fulfilled" ? results[0].value : null;
  state.status = results[1].status === "fulfilled" ? results[1].value : null;
  state.verified = results[2].status === "fulfilled" ? results[2].value.signals : [];
  // One operational MySQL view; show every stored measurement.

  const mode = document.getElementById("mode-indicator");
  if (mode) {
    const empty = !state.status?.has_real_samples;
    mode.textContent = empty ? "Очікування показників" : "Дані MySQL";
    mode.className = "mode-indicator " + (empty ? "mode-pending" : "mode-real");
    mode.title = empty ? "У базі ще немає вимірювань" : "Показники з бази MySQL";
  }
  // Styling and data selection use the same operational mode.
  updateConnection(state.status !== null);
  if (results[3].status === "fulfilled") {
    const auth=results[3].value;
    state.role=auth.role || "viewer";state.csrf=auth.csrf || null;
    createLogout(auth);
  }
  const pair=results[4].status === "fulfilled" ? results[4].value.devices : [];
  try {
    const stored=typeof localStorage !== "undefined" ?
      localStorage.getItem("rakurs.selectedDeye") : null;
    const found=(pair||[]).find(x=>x.device_key===stored);
    if(found){state.selectedDeviceKey=stored;state.selectedSlotNo=Number(found.slot_no||1);}
  }catch(_error){}
  await render();
}
window.addEventListener("hashchange", render);
boot();
