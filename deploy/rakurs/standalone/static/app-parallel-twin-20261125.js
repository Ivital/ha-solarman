"use strict";
// Rakurs standalone: the ONLY data API is this app's /api/v1/* backed by MySQL.
const base = "/api/v1";
let DEMO_MODE = new URLSearchParams(window.location.search).get("demo") === "1";
const FORCE_REAL_MODE = new URLSearchParams(window.location.search).get("real") === "1";
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
  ["system", "Система", "⚙"]
];
const state = { catalog: null, status: null, verified: [], route: "overview", cycle: 0 };
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
async function api(path) {
  const selection = FORCE_REAL_MODE ? "real=1" : (DEMO_MODE ? "demo=1" : "");
  const url = base + path + (selection ? (path.includes("?") ? "&" : "?") + selection : "");
  const response = await fetch(url, {cache: "no-store", credentials: "same-origin"});
  if (!response.ok) throw new Error("Помилка отримання даних MySQL (" + response.status + ")");
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
    ["НАЛАШТУВАННЯ", ["modbus", "system"]]
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
  if (DEMO_MODE) return;
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
  add(head, element("span", "kpi-icon", icon), element("span", "kpi-state",
    value === null ? "Немає даних" : "Є запис"));
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
    "Схема показує сумарні потоки, а не розподіл енергії між окремими споживачами.");
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
  section("Збережений зріз", DEMO_MODE ?
    "Архів одного профілю · не сумарні показники двох Deye" :
    "Останні показники з MySQL · час Київ");
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
    element("span", "", DEMO_MODE ?
      "Записи MySQL · фізичне походження не підтверджене" :
      "Дані реальних приладів"));
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
      if (numeric.length < 2) {
        body.appendChild(createEmpty("Недостатньо даних", "Потрібні щонайменше дві точки."));
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
  section(title, items.length + " параметрів · останні збережені показники");
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
  const polyline = document.createElementNS(svg.namespaceURI, "polyline");
  const pointsString = real.map(p => {
    const x = 52 + p.index * 816 / Math.max(1, points.length - 1);
    const y = 238 - (p.v - low) * 198 / (high - low);
    return x.toFixed(2) + "," + y.toFixed(2);
  }).join(" ");
  polyline.setAttribute("points", pointsString);
  svg.appendChild(polyline);
  const first = points[real[0].index];
  const last = points[real[real.length - 1].index];
  if (first && last) {
    const labels = [
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
  section(archived ? "Архів вимірювань" : "Історія показників",
    "Графік та журнал окремого сигналу · час Київ");
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
         entry.quality === "simulated" ? "Архів MySQL · джерело не підтверджене" : entry.quality || "—"
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
async function renderTwinSummary(cycle) {
  // The original overview KPIs are from ONE legacy synthetic device.
  // Independent 20 kW twin devices must be visible on the overview.
  let profile;
  try {
    profile = await api("/parallel/simulation");
  } catch (_error) {
    return;
  }
  if (!current(cycle) || !profile?.simulation || !profile.storage_ready ||
      !Array.isArray(profile.devices) || profile.devices.length !== 2) return;
  section("Два інвертори Deye", "Окремі записи кожного профілю · MySQL");
  const grid = element("div", "parallel-home-grid");
  main.appendChild(grid);
  const results = await Promise.allSettled(profile.devices.map(d =>
    api("/parallel/simulation/latest?device=" + encodeURIComponent(d.device_key))));
  if (!current(cycle)) return;
  profile.devices.forEach((d, i) => {
    const card = element("article", "parallel-device parallel-home-device");
    const title = element("div", "parallel-device-head");
    add(title, element("strong", "parallel-device-name", "Deye №" + d.slot_no),
      element("span", "parallel-state", "АРХІВ MYSQL"));
    card.appendChild(title);
    card.appendChild(element("p", "parallel-device-model",
      "SUN-20K-SG05LP3-EU-SM2 · 20 кВт номінально"));
    const values = results[i].status === "fulfilled" &&
      results[i].value.simulation ? (results[i].value.values || {}) : {};
    const metrics = element("div", "parallel-metric-list");
    for (const [key, label] of [
      ["pv_power", "PV"], ["battery_power", "АКБ"], ["battery_soc", "SOC"]
    ]) {
      const item = values[key];
      const valid = item && item.quality === "simulated" &&
        item.value_decimal !== null && item.value_decimal !== undefined;
      const tile = element("div", "parallel-reading");
      add(tile, element("small", "", label),
        element("strong", "", valid ?
          number(item.value_decimal) + " " + (item.unit || "") : "—"),
        element("span", "", "Запис MySQL · джерело не підтверджене"));
      metrics.appendChild(tile);
    }
    card.appendChild(metrics);
    const footer = element("div", "parallel-home-foot");
    footer.appendChild(element("span", "", "Сирі регістри: " +
      d.raw_registers + " · поля Modbus: " + d.decoded_fields));
    const link = element("a", "parallel-open-link", "Переглянути обидва інвертори");
    link.href = "#/parallel";
    footer.appendChild(link);
    card.appendChild(footer);
    grid.appendChild(card);
  });
  main.appendChild(element("p", "parallel-readonly-note",
    "Походження цих значень не підтверджене фізичним опитуванням. Спільна АКБ " +
    "не має підтвердженого SOC; відсотки двох профілів не додаються."));
}

async function renderParallel(cycle) {
  section("Два інвертори — одна система",
    "Deye SUN-20K-SG05LP3-EU-SM2 · спільна акумуляторна батарея");
  // Show populated database data BEFORE unconnected physical placeholders.
  await renderParallelDemo(cycle);
  if (!current(cycle)) return;
  section("Фізичні інвертори — ще не підключені",
    "Профілі фізичних Deye · підтверджених вимірювань поки немає");
  const intro = panel("Паралельна система · 2 × 20 кВт",
    "40 кВт — номінальна паспортна потужність двох інверторів, а не поточна генерація.");
  intro.className += " parallel-intro";
  const note = element("p", "parallel-warning",
    "Фізичне підключення, ролі Master/Slave, адреси Modbus і джерело SOC ще не підтверджені. " +
    "Rakurs не змінює налаштувань інверторів та не виконує команди запису.");
  intro.appendChild(note);
  main.appendChild(intro);
  const detail = element("div", "parallel-devices");
  main.appendChild(detail);
  const battery = panel("Спільний акумулятор 48 В",
    "Одна система зберігання енергії для обох інверторів");
  battery.className += " parallel-shared";
  add(battery, element("div", "shared-soc", "SOC: —"),
    element("p", "subtle",
      "Відсоток заряду не можна додавати. Він з'явиться після перевірки джерела BMS; " +
      "дані одного інвертора не видаються за підтверджені загальні."));
  main.appendChild(battery);
  const systemNote = panel("Загальні показники",
    "Суми енергії доступні лише після перевірки незалежних джерел і CT.");
  const aggregate = element("div", "parallel-aggregate",
    "Поточна сумарна потужність: — · Підтверджених джерел: 0 із 2");
  systemNote.appendChild(aggregate);
  main.appendChild(systemNote);
  try {
    const response = await api("/parallel");
    if (!current(cycle)) return;
    const devices = response.devices || [];
    aggregate.textContent = "Поточна сумарна потужність: — · Зареєстровано інверторів: " +
      response.registered_inverters + " із " + response.expected_inverters +
      ". Агрегація не підтверджена.";
    const slots = Math.max(response.expected_inverters, devices.length);
    for (let i = 0; i < slots; i++) {
      const data = devices[i] || null;
      const card = element("article", "parallel-device");
      const head = element("div", "parallel-device-head");
      add(head, element("div", "parallel-device-name",
        "Інвертор №" + (i + 1)),
        element("span", "parallel-state", data ?
          "Зареєстрований · канал не перевірено" : "Не зареєстрований"));
      card.appendChild(head);
      card.appendChild(element("p", "parallel-device-model", response.model));
      const facts = element("div", "parallel-facts");
      const entries = [
        ["Роль у паралелі", "Не підтверджена"],
        ["Адреса Modbus", "Не визначена"],
        ["Канал моніторингу", "Не перевірений"],
        ["Серійний номер", data && data.serial_number ? data.serial_number : "Не встановлений"],
        ["ID у базі", data ? data.device_key : "Не прив'язаний"]
      ];
      for (const [k, v] of entries) {
        const pair = element("div", "parallel-fact");
        add(pair, element("small", "", k), element("strong", "", v));
        facts.appendChild(pair);
      }
      card.appendChild(facts);
      const measures = element("div", "parallel-readings");
      const keys = [
        ["pv_power", "Сонце"],
        ["battery_power", "АКБ"],
        ["inverter_temperature", "Температура"]
      ];
      if (!data) {
        card.appendChild(createEmpty("Зчитування ще не налаштоване",
          "Rakurs не підставляє архівні значення як вимірювання іншого інвертора."));
        detail.appendChild(card);
        continue;
      }
      const place = element("div", "parallel-metric-list");
      card.appendChild(place);
      detail.appendChild(card);
      const deviceQuery = "?device=" + encodeURIComponent(data.device_key);
      const [latest, events, history, registers] = await Promise.allSettled([
        api("/parallel/latest" + deviceQuery),
        api("/parallel/events" + deviceQuery),
        api("/parallel/history" + deviceQuery + "&key=pv_power&hours=24"),
        api("/parallel/modbus-fields" + deviceQuery)
      ]);
      if (!current(cycle)) return;
      if (latest.status !== "fulfilled") {
        place.appendChild(createEmpty("Значення недоступні",
          "Показники для цього інвертора не вдалося прочитати."));
      } else {
        const values = latest.value.values || {};
        for (const [key, label] of keys) {
          const reading = values[key];
          const present = reading && reading.value_decimal !== null &&
            reading.value_decimal !== undefined && reading.quality !== "simulated";
          const cell = element("div", "parallel-reading");
          add(cell, element("small", "", label),
            element("strong", "", present ?
              number(reading.value_decimal) + " " + (reading.unit || "") : "—"),
            element("span", "", present ?
              "Останній запис: " + time(reading.observed_at_utc) : "Вимірювання відсутнє"));
          place.appendChild(cell);
        }
      }
      const eventLabel = events.status === "fulfilled" ?
        "Подій у журналі: " + (events.value.events || []).length :
        "Журнал подій недоступний";
      card.appendChild(element("p", "parallel-device-events", eventLabel));
      card.appendChild(element("p", "parallel-device-events",
        registers.status === "fulfilled" ?
        "Перевірених полів Modbus: " + registers.value.count :
        "Реєстр Modbus недоступний"));
      const chartBox = element("section", "parallel-device-chart");
      chartBox.appendChild(element("h3", "", "Генерація PV · останні 24 години"));
      if (history.status !== "fulfilled") {
        chartBox.appendChild(createEmpty("Графік недоступний",
          "Історію інвертора не вдалося прочитати."));
      } else {
        const points = history.value.points || [];
        if (points.filter(point => point.value_decimal !== null).length < 2) {
          chartBox.appendChild(createEmpty("Ще немає підтвердженої історії",
            "Графік не підміняється вигаданими даними."));
        } else {
          chartBox.appendChild(svgChart(points, "кВт"));
        }
      }
      card.appendChild(chartBox);
    }
    if (!devices.length) {
      const explain = element("p", "parallel-readonly-note",
        "У базі є архівні записи, однак вони не використовуються як " +
        "підтвердження фізичного інвертора №1 чи №2.");
      main.appendChild(explain);
    }
  } catch (_error) {
    detail.appendChild(createEmpty("Не вдалося перевірити облік двох інверторів",
      "Джерело даних недоступне. Схема та значення не припускаються."));
  }
}

async function renderParallelDemo(cycle) {
  section("Архів двох інверторів",
    "Збережені записи MySQL · фізичне джерело не підтверджене");
  const scope = panel("Архівні записи",
    "Перевірка повноти архіву для кожного з двох профілів Deye.");
  scope.className += "parallel-demo-panel";
  const intro = element("p", "parallel-warning",
    "Походження цих значень не підтверджене фізичним опитуванням. Їх не можна використовувати " +
    "для керування живленням, розрахунків енергії чи діагностики аварій.");
  scope.appendChild(intro);
  main.appendChild(scope);
  const cards = element("div", "parallel-devices");
  scope.appendChild(cards);
  let payload;
  try {
    payload = await api("/parallel/simulation");
  } catch (_error) {
    if (current(cycle)) cards.appendChild(createEmpty("Архів недоступний",
      "Перевірте наявність таблиць MySQL для збереження Modbus."));
    return;
  }
  if (!current(cycle)) return;
  const devices = payload.devices || [];
  if (!payload.storage_ready || devices.length !== 2) {
    cards.appendChild(createEmpty("Архів двох інверторів ще не заповнений",
      "Не знайдено двох повних наборів архівних записів у MySQL. " +
      "Для їх відображення потрібні записи з відповідних джерел."));
    return;
  }
  for (const device of devices) {
    const card = element("article", "parallel-device");
    const title = element("div", "parallel-device-head");
    add(title, element("strong", "parallel-device-name",
      "Deye №" + device.slot_no),
      element("span", "parallel-state", "АРХІВ MYSQL"));
    card.appendChild(title);
    card.appendChild(element("p", "parallel-device-model",
      "SUN-20K-SG05LP3-EU-SM2 · архів MySQL"));
    const summary = element("div", "parallel-demo-counters");
    for (const [name, count] of [
      ["Сирі регістри", device.raw_registers],
      ["Поля Modbus", device.decoded_fields],
      ["Показники", device.ui_fields]
    ]) {
      const tile = element("div", "parallel-demo-counter");
      add(tile, element("strong", "", String(count || 0)),
        element("small", "", name));
      summary.appendChild(tile);
    }
    card.appendChild(summary);
    const previews = element("div", "parallel-metric-list");
    card.appendChild(previews);
    cards.appendChild(card);
    const query = encodeURIComponent(device.device_key);
    try {
      const result = await api("/parallel/simulation/latest?device=" + query);
      if (!current(cycle)) return;
      const keys = [
        ["pv_power", "PV"], ["battery_soc", "SOC"], ["battery_power", "АКБ"]
      ];
      for (const [key, label] of keys) {
        const reading = result.values && result.values[key];
        const ready = reading && reading.quality === "simulated" &&
          reading.value_decimal !== null && reading.value_decimal !== undefined;
        const tile = element("div", "parallel-reading");
        add(tile, element("small", "", label),
          element("strong", "", ready ?
            number(reading.value_decimal) + " " + (reading.unit || "") : "—"),
          element("span", "", "Запис MySQL"));
        previews.appendChild(tile);
      }
    } catch (_error) {
      previews.appendChild(createEmpty("Архівні значення недоступні",
        "Перевірка архіву не звертається до обладнання."));
    }
    const rawSection = element("section", "parallel-raw-viewer");
    rawSection.appendChild(element("h3", "", "Повний архів сирих регістрів"));
    const table = element("div", "parallel-raw-table");
    rawSection.appendChild(table);
    const button = element("button", "more-button", "Показати регістри (200)");
    button.type = "button";
    rawSection.appendChild(button);
    let offset = 0;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const page = await api("/parallel/simulation/raw?device=" + query +
          "&offset=" + offset + "&limit=200");
        if (!current(cycle)) return;
        const rows = page.raw_registers || [];
        for (const item of rows) {
          const row = element("div", "parallel-raw-row");
          add(row, element("code", "", String(item.register_address)),
            element("span", "", "FC" + item.function_code),
            element("strong", "", String(item.raw_uint16)),
            element("small", "", "архів"));
          table.appendChild(row);
        }
        offset += rows.length;
        button.textContent = "Показано " + offset + " із " + page.total +
          (offset < page.total ? " · ще 200" : " · усі записи");
        button.hidden = offset >= page.total;
      } catch (_error) {
        table.appendChild(createEmpty("Не вдалося прочитати сирі регістри",
          "Повторіть спробу після перевірки MySQL."));
      } finally {
        if (current(cycle)) button.disabled = false;
      }
    });
    card.appendChild(rawSection);
    const decoded = element("section", "parallel-raw-viewer parallel-field-viewer");
    decoded.appendChild(element("h3", "",
      "Розшифровані поля Modbus — " + device.decoded_fields + " записів"));
    const decodedRows = element("div", "parallel-raw-table");
    decoded.appendChild(decodedRows);
    const fieldButton = element("button", "more-button", "Переглянути поля Modbus (80)");
    fieldButton.type = "button";
    decoded.appendChild(fieldButton);
    let fieldOffset = 0;
    fieldButton.addEventListener("click", async () => {
      fieldButton.disabled = true;
      try {
        const result = await api("/parallel/simulation/fields?device=" + query +
          "&offset=" + fieldOffset + "&limit=80");
        if (!current(cycle)) return;
        const items = result.fields || [];
        for (const entry of items) {
          const row = element("div", "parallel-field-row");
          const shown = entry.value_decimal !== null && entry.value_decimal !== undefined ?
            number(entry.value_decimal) : entry.value_text || "—";
          add(row,
            element("code", "", entry.field_key),
            element("span", "", entry.field_name),
            element("strong", "", shown + " " + (entry.unit || "")),
            element("small", "", "АРХІВ MYSQL"));
          decodedRows.appendChild(row);
        }
        fieldOffset += items.length;
        fieldButton.textContent = "Показано " + fieldOffset + " із " + result.total +
          (fieldOffset < result.total ? " · ще 80" : " · усі записи");
        fieldButton.hidden = fieldOffset >= result.total;
      } catch (_error) {
        decodedRows.appendChild(createEmpty("Поля Modbus недоступні",
          "Перевірте стан API та журнал сервера."));
      } finally {
        if (current(cycle)) fieldButton.disabled = false;
      }
    });
    card.appendChild(decoded);
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
          " · " + (record.device_key || "Невідомий пристрій")));
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
      const simulated = batch.status === "simulated";
      const title = code === "DEYE_HISTORY_72H_V1" ?
        "Додано історію вимірювань за 72 години" :
        simulated ? "Завантажено архівний набір" :
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
      add(stamp, element("span", "event-severity", simulated ?
        "Архів MySQL" : (batch.status || "Без статусу")),
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
      fields.forEach(field => row.appendChild(element("td", "", field.endsWith("_utc") ? time(item[field]) : (item[field] ?? "—"))));
      tbody.appendChild(row);
    });
    add(table, thead, tbody); wrap.appendChild(table); root.appendChild(wrap);
  } catch (_error) { if (current(cycle)) root.appendChild(createEmpty("База недоступна", "Не вдалося прочитати записи.")); }
}
async function renderModbus(cycle) {
  addStatusMessage();
  section("Довідник Modbus", "Технічні параметри · лише читання");
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
  section("Конфігурація системи", DEMO_MODE ? "Архівні дані" :
                                                      "Статус незалежного Rakurs");
  const pane = panel("Єдина MySQL");
  const info = element("div", "kv");
  [
    ["Сервер", "192.168.5.30"],
    ["База", "rakurs"],
    ["Джерело", DEMO_MODE ? "MySQL · походження не підтверджене" : "MySQL"],
    ["Пристрої", state.status ? state.status.registered_devices : "—"],
    [DEMO_MODE ? "Параметри архіву" : "Підтверджені сигнали",
      state.status ? (DEMO_MODE ? state.status.simulated_signals :
                                  state.status.verified_signals) : "—"],
    ["Колектор Deye", "Не запущений"]
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
  if (state.route === "overview" && SSR_PRESENT) {
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
        // when verified twin simulation endpoints respond.
        if (!state.twinFallbackCycle || state.twinFallbackCycle !== cycle) {
          state.twinFallbackCycle = cycle;
          await renderTwinSummary(cycle);
        }
        return;
      }
      primedValues = values;
      if (!FORCE_REAL_MODE && critical.some(key => values[key].quality === "simulated")) {
        DEMO_MODE = true; // source metadata always wins over stale mode state
      }
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
  }
}
async function boot() {
  // Always load the database status before selecting a display mode. When
  // only synthetic samples exist, use them on the ordinary Rakurs homepage.
  const results = await Promise.allSettled([
    api("/catalog"), api("/status"), api("/signals"), api("/auth")
  ]);
  state.catalog = results[0].status === "fulfilled" ? results[0].value : null;
  state.status = results[1].status === "fulfilled" ? results[1].value : null;
  state.verified = results[2].status === "fulfilled" ? results[2].value.signals : [];
  // The API is the source of truth: never label synthetic values as verified.
  DEMO_MODE = state.status ? state.status.mode === "simulation" :
    (SSR_MODE === "simulation" && !FORCE_REAL_MODE);

  const mode = document.getElementById("mode-indicator");
  if (mode) {
    const awaitingReal = !DEMO_MODE && !state.status?.has_real_samples;
    mode.textContent = DEMO_MODE ? "Архів MySQL" :
      awaitingReal ? "Очікування реальних даних" : "Реальні вимірювання";
    mode.className = "mode-indicator " + (DEMO_MODE ? "mode-simulation" :
      awaitingReal ? "mode-pending" : "mode-real");
    mode.title = DEMO_MODE ?
      "Архівні записи MySQL не підтверджені фізичним опитуванням" :
      awaitingReal ? "Підтверджених реальних вимірювань поки немає" :
      "Дані, отримані з підключених вимірювальних приладів";
  }
  if (DEMO_MODE) document.body.classList.add("demo-mode");
  updateConnection(state.status !== null);
  if (results[3].status === "fulfilled") createLogout(results[3].value);
  await render();
}
window.addEventListener("hashchange", render);
boot();
