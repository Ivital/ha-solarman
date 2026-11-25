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
  ["history", "Історія", "▥"],
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
  for (const view of views) {
    const item = element("a", state.route === view[0] ? "active" : "");
    item.href = "#/" + view[0];
    add(item, element("span", "symbol", view[2]), element("span", "", view[1]));
    item.title = view[1];
    navigation.appendChild(item);
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
function metricCard(label, icon, key, values) {
  const card = element("article", "card");
  const data = values[key];
  const actual = data && data.value_decimal !== null;
  const val = element("div", "value", actual ? number(data.value_decimal) : "—");
  if (actual && data.unit) val.appendChild(element("span", "unit", data.unit));
  add(card, element("div", "card-icon", icon), element("h3", "", label), val,
      element("div", "detail" + (actual ? " verified" : ""),
        actual ? (DEMO_MODE ? "АРХІВ MYSQL · " : "З MySQL · ") +
          time(data.observed_at_utc) + " · " + (data.quality || "якість не вказана")
          : "Підтвердженого вимірювання немає"));
  return card;
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
  const descriptors = [
    ["Генерація PV", "☀"], ["Навантаження", "⌂"], ["Потужність мережі", "⌁"],
    ["Заряд АКБ", "▣"], ["Потужність АКБ", "↯"], ["Температура інвертора", "♨"],
    ["Виробіток за добу", "◉"], ["Споживання за добу", "▥"]
  ];
  section("Оперативні показники", DEMO_MODE ? "Збережені показники MySQL · архів" :
                                                    "Фактичні показники MySQL");
  const deck = element("div", "cards");
  main.appendChild(deck);
  let values = primedValues || {};
  if (!primedValues) {
    try {
      values = (await api("/latest?keys=" + encodeURIComponent(keys.join(",")))).values || {};
    } catch (_error) {
      if (current(cycle)) message("Показники недоступні", "Не вдалося прочитати останні вимірювання з MySQL.", true);
    }
  }
  if (!current(cycle)) return;
  keys.forEach((key, i) => deck.appendChild(metricCard(descriptors[i][0], descriptors[i][1], key, values)));
  const grid = element("div", "grid-two");
  const flow = panel("Енергетичний баланс", DEMO_MODE ?
    "Потоки за останніми записаними значеннями" :
    "Напрямки потоків покажемо після підтвердження регістрів Deye.");
  if (DEMO_MODE) {
    const balance = element("div", "kv");
    for (const pair of [
      ["Сонце", "pv_power"], ["Акумулятор", "battery_power"],
      ["Мережа", "grid_power"], ["Навантаження", "load_power"]
    ]) {
      const item = element("div", "");
      const reading = values[pair[1]];
      add(item, element("strong", "", reading ? number(reading.value_decimal) : "—"),
        element("small", "", pair[0] + " · кВт"));
      balance.appendChild(item);
    }
    flow.appendChild(balance);
  } else {
    flow.appendChild(createEmpty("Схема очікує телеметрію",
      "Показники генерації, АКБ, мережі та навантаження не моделюються."));
  }
  const quality = panel("Повнота обліку", DEMO_MODE ?
      "Параметри збереженого знімка" :
      "Обліковуємо лише перевірені сигнали та реальні вимірювання.");
  const info = element("div", "kv");
  [
    ["Пристрої", state.status ? state.status.registered_devices : "—"],
    [DEMO_MODE ? "Архівні сигнали" : "Підтверджені сигнали",
      state.status ? (DEMO_MODE ? state.status.simulated_signals : state.status.verified_signals) : "—"],
    ["Планові сигнали", planCount()]
  ].forEach(pair => {
    const item = element("div", "");
    add(item, element("strong", "", pair[1]), element("small", "", pair[0]));
    info.appendChild(item);
  });
  quality.appendChild(info);
  add(grid, flow, quality);
  main.appendChild(grid);

  // Trend charts live on the existing Rakurs homepage. They read the same
  // authenticated MySQL archive as the dedicated History and Archive views.
  section("Динаміка за 24 години", DEMO_MODE ?
    "Історичні значення MySQL · архів" : "Підтверджені вимірювання MySQL");
  const trends = element("div", "trend-grid");
  main.appendChild(trends);
  const series = [
    ["pv_power", "Сонячна генерація", "кВт"],
    ["load_power", "Навантаження", "кВт"],
    ["battery_soc", "Заряд акумулятора", "%"]
  ];
  const chartBodies = series.map(([key, label, unit]) => {
    const box = panel(label, "Останні 24 години · " + unit);
    const body = element("div", "trend-content");
    box.appendChild(body);
    trends.appendChild(box);
    return body;
  });
  const responses = await Promise.allSettled(series.map(([key]) =>
    api("/history?key=" + encodeURIComponent(key) + "&hours=24")));
  if (!current(cycle)) return;
  responses.forEach((response, index) => {
    const body = chartBodies[index];
    if (response.status !== "fulfilled") {
      body.appendChild(createEmpty("Динаміка недоступна", "Помилка читання MySQL."));
      return;
    }
    const points = response.value.points || [];
    if (points.filter(x => x.value_decimal !== null).length < 2) {
      body.appendChild(createEmpty("Недостатньо точок",
        "Для графіка необхідно щонайменше два часові значення."));
      return;
    }
    body.appendChild(svgChart(points, series[index][2]));
    body.appendChild(element("p", "subtle", "Точок у MySQL: " + points.length));
  });
}
async function renderGroup(groups, title, cycle) {
  addStatusMessage();
  const items = groupItems(groups);
  section(title, DEMO_MODE ? "Архів · " + items.length + " полів каталогу" :
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
        ? (DEMO_MODE ? "АРХІВНИЙ сигнал" : "Підтверджений сигнал") :
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
          DEMO_MODE ? "Збережені часові ряди · час Київ" :
                      "Час даних — UTC; відображення — Київ");
  const available = state.verified;
  if (!available.length) {
    main.appendChild(createEmpty("Немає підтверджених сигналів",
      "Щоб будувати історію, потрібно підключити Deye та підтвердити його регістри. Порожня БД не підміняється вигаданими значеннями."));
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
      DEMO_MODE ? "Динаміка збережених архівних значень" : "Графік фактичних вимірювань"));
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
  section("Повний перелік Modbus", DEMO_MODE ?
    "Профіль P3 · збережений знімок" : "Профіль P3 · довідник регістрів");
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

  const switcher = element("span", "tag", "Архів MySQL");
  switcher.title = "Збережені записи MySQL; походження фізичних вимірювань не підтверджено";
  const actions = document.querySelector(".top-actions");
  if (actions) actions.appendChild(switcher);
  if (DEMO_MODE) document.body.classList.add("demo-mode");
  updateConnection(state.status !== null);
  if (results[3].status === "fulfilled") createLogout(results[3].value);
  await render();
}
window.addEventListener("hashchange", render);
boot();
