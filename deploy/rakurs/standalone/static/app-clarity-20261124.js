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
    ["МОНІТОРИНГ", ["overview", "pv", "battery", "grid", "load", "inverter"]],
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
  document.getElementById("updated").textContent = "Оновлено: " + time(new Date().toISOString());
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
    value === null ? "Немає даних" : DEMO_MODE ? "Тестові дані" : "З MySQL"));
  card.appendChild(head);
  card.appendChild(element("h3", "", label));
  const numberRow = element("div", "kpi-reading");
  add(numberRow, element("strong", "", pretty(value)),
    element("span", "unit", value !== null ? (data.unit || "") : ""));
  card.appendChild(numberRow);
  if (key === "battery_soc" && value !== null) {
    const meter = element("div", "soc-meter");
    meter.setAttribute("role", "meter");
    meter.setAttribute("aria-label", "Рівень заряду акумулятора");
    meter.setAttribute("aria-valuemin", "0");
    meter.setAttribute("aria-valuemax", "100");
    meter.setAttribute("aria-valuenow", String(value));
    const fill = element("div", "soc-fill");
    fill.style.width = Math.min(100, Math.max(0, value)) + "%";
    meter.appendChild(fill);
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
  section("Зараз", "Останні збережені показники · час Київ");
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
      "Тестові вимірювання — не фактичні показники обладнання" :
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
  section(title, DEMO_MODE ? "Демо · " + items.length + " полів каталогу" :
                                              items.length + " запланованих полів · без вигаданих даних");
  const tags = element("div", "tags");
  add(tags, element("span", "tag", "Джерело: MySQL rakurs"));
  main.appendChild(tags);
  if (!items.length) { main.appendChild(createEmpty("Каталог недоступний", "Не вдалося прочитати реєстр планових сигналів.")); return; }
  const verified = verifiedKeys();
  const keys = items.filter(i => verified.has(i.key)).map(i => i.key);
  let values = {};
  for (let offset = 0; offset < keys.length; offset += 24) {
    const chunk = keys.slice(offset, offset + 24);
    try {
      const batch = (await api("/latest?keys=" + encodeURIComponent(chunk.join(",")))).values;
      Object.assign(values, batch);
    } catch (_error) {
      if (current(cycle)) message("Дані тимчасово недоступні",
        "Не вдалося отримати значення MySQL.", true);
      break;
    }
  }
  if (!current(cycle)) return;
  const list = element("div", "metric-list");
  for (const metric of items) {
    const record = values[metric.key];
    const hasValue = record && record.value_decimal !== null;
    const row = element("div", "metric");
    const info = element("div", "");
    add(info, element("strong", "", metric.label),
      element("small", "", verified.has(metric.key)
        ? (DEMO_MODE ? "СИМУЛЬОВАНИЙ сигнал" : "Підтверджений сигнал") :
          "Плановий · ще не перевірено"));
    const value = element("div", "number" + (hasValue ? "" : " unknown"),
      hasValue ? number(record.value_decimal) + " " + (record.unit || "") : "—");
    value.title = hasValue ? time(record.observed_at_utc) : "Немає підтвердженого значення";
    add(row, info, value);
    list.appendChild(row);
  }
  main.appendChild(list);
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
  return svg;
}
async function renderHistory(cycle, archived) {
  addStatusMessage();
  section(archived ? "Архів вимірювань" : "Історія показників",
          DEMO_MODE ? "Збережені часові ряди · симуляція · час Київ" :
                      "Час даних — UTC; відображення — Київ");
  const available = state.verified;
  if (!available.length) {
    main.appendChild(createEmpty("Немає підтверджених сигналів",
      "Щоб будувати історію, потрібно підключити Deye та підтвердити його регістри. Порожня БД не підміняється тестовими даними."));
    return;
  }
  const controls = element("div", "controls");
  const picker = element("select", "");
  for (const s of available) {
    const option = element("option", "", s.label_uk + " (" + s.signal_key + ")");
    option.value = s.signal_key;
    if (s.signal_key === "pv_power") option.selected = true;
    picker.appendChild(option);
  }
  const range = element("select", "");
  for (const hours of [1, 6, 12, 24, 72, 168]) {
    const opt = element("option", "", hours + " год");
    opt.value = String(hours);
    if (hours === 24) opt.selected = true;
    range.appendChild(opt);
  }
  add(controls, picker, range);
  main.appendChild(controls);
  const body = panel(archived ? "Історичні записи MySQL" : "Динаміка за вибраний період");
  main.appendChild(body);
  async function refresh() {
    clear(body);
    add(body, element("h2", "", archived ? "Історичні записи MySQL" :
      DEMO_MODE ? "Динаміка збережених симульованих значень" : "Графік фактичних вимірювань"));
    try {
      const result = await api("/history?key=" + encodeURIComponent(picker.value) + "&hours=" + range.value);
      if (!current(cycle)) return;
      if (!result.points.length) {
        body.appendChild(createEmpty("Немає записів", "За вибраний період значень у MySQL немає."));
        return;
      }
      body.appendChild(svgChart(result.points, (result.signal && result.signal.unit) || ""));
      body.appendChild(element("p", "subtle", "Отримано " + result.points.length +
        (result.truncated ? " (вибірка обмежена останніми 1000 точками)" : "") + " записів з MySQL."));
      const table = element("div", "table-wrap");
      const head = element("thead", ""), hr = element("tr", "");
      ["Час (Київ)", "Значення", "Якість"].forEach(t => hr.appendChild(element("th", "", t)));
      head.appendChild(hr);
      const tb = element("tbody", "");
      for (const entry of result.points.slice(-25).reverse()) {
        const tr = element("tr", "");
        [time(entry.observed_at_utc), number(entry.value_decimal), entry.quality].forEach(v => tr.appendChild(element("td", "", v)));
        tb.appendChild(tr);
      }
      const tbl = element("table", "data-table");
      add(tbl, head, tb); add(table, tbl); body.appendChild(table);
    } catch (_error) {
      if (current(cycle)) body.appendChild(createEmpty("Історія недоступна", "Перевірте підключення до MySQL."));
    }
  }
  picker.addEventListener("change", refresh);
  range.addEventListener("change", refresh);
  await refresh();
}
async function renderTable(path, sectionName, headings, fields, cycle) {
  addStatusMessage();
  section(sectionName, DEMO_MODE ? "Демо · MySQL" :
                                        "Записи виключно з MySQL rakurs");
  const root = panel(sectionName);
  main.appendChild(root);
  try {
    const payload = await api(path);
    if (!current(cycle)) return;
    const source = payload.events || payload.devices || payload.batches || [];
    if (!source.length) { root.appendChild(createEmpty("Записів поки немає", "Дані не вигадуються й не генеруються для демонстрації.")); return; }
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
  section("Повний перелік Modbus", DEMO_MODE ?
    "Профіль P3 · один тестовий знімок" : "Профіль P3 · довідник регістрів");
  const pane = panel("Параметри Deye", "Найменування, адреси, значення та одиниці");
  main.appendChild(pane);
  try {
    const payload = await api("/modbus-fields");
    if (!current(cycle)) return;
    const summary = element("p", "subtle",
      "Параметрів: " + payload.count + " · Адрес у профілі: " + payload.register_count);
    pane.appendChild(summary);
    const wrap = element("div", "table-wrap");
    const table = element("table", "data-table");
    const head = element("thead", ""), tr = element("tr", "");
    ["Група", "Параметр", "Адреса Modbus", "Значення", "Одиниця"].forEach(t =>
      tr.appendChild(element("th", "", t)));
    head.appendChild(tr);
    const body = element("tbody", "");
    for (const field of payload.fields) {
      const row = element("tr", "");
      let regs = field.registers_json || "[]";
      try {
        regs = JSON.parse(regs);
        regs = regs.map(r => "0x" + Number(r).toString(16).toUpperCase().padStart(4, "0"))
          .join(", ");
      } catch (_error) {
        regs = String(regs);
      }
      const value = field.value_text !== null && field.value_text !== undefined ?
        field.value_text : number(field.value_decimal);
      [field.group_name, field.field_name, regs || "Розрахунковий",
       value, field.unit || ""].forEach(v => row.appendChild(element("td", "", v)));
      body.appendChild(row);
    }
    add(table, head, body); wrap.appendChild(table); pane.appendChild(wrap);
  } catch (_error) {
    pane.appendChild(createEmpty("Не вдалося завантажити реєстр", "Перевірте MySQL."));
  }
}

async function renderSystem(cycle) {
  addStatusMessage();
  section("Конфігурація системи", DEMO_MODE ? "Демо режим" :
                                                      "Статус незалежного Rakurs");
  const pane = panel("Єдина MySQL");
  const info = element("div", "kv");
  [
    ["Сервер", "192.168.5.30"],
    ["База", "rakurs"],
    ["Джерело", DEMO_MODE ? "MySQL · СИМУЛЯЦІЯ" : "MySQL"],
    ["Пристрої", state.status ? state.status.registered_devices : "—"],
    [DEMO_MODE ? "Тестові параметри" : "Підтверджені сигнали",
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
      }
      return;
    }
  }
  if (!current(cycle)) return;
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
    case "history": await renderHistory(cycle, false); break;
    case "events": await renderTable("/events", "Події", ["Час", "Рівень", "Код", "Повідомлення", "Пристрій"],
      ["observed_at_utc", "severity", "event_code", "message", "device_key"], cycle); break;
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

  const switcher = element("a", "tag",
      FORCE_REAL_MODE ? "Показати наявні дані" :
      DEMO_MODE ? "Реальні вимірювання" : "Дані Deye");
  switcher.href = FORCE_REAL_MODE ? "/" :
      DEMO_MODE ? "/?real=1" : "/?demo=1";
  switcher.title = DEMO_MODE ? "Показувати лише показники реального інвертора" :
      "Показати наявні записи MySQL";
  const actions = document.querySelector(".top-actions");
  if (actions) actions.appendChild(switcher);
  if (DEMO_MODE) document.body.classList.add("demo-mode");
  updateConnection(state.status !== null);
  if (results[3].status === "fulfilled") createLogout(results[3].value);
  await render();
}
window.addEventListener("hashchange", render);
boot();
