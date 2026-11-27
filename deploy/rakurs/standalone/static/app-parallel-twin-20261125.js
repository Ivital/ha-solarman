"use strict";
// Rakurs standalone: the ONLY data API is this app's /api/v1/* backed by MySQL.
const base = "/api/v1";
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
  ["registers", "Регістри V105.3", "▥"],
  ["system", "Система", "⚙"],
  ["settings", "Керування", "⚙"]
];
const state = { catalog: null, status: null, verified: [], route: "overview", cycle: 0,
  selectedDeviceKey: null, selectedSlotNo: 1, role: "viewer", csrf: null, navQuery: "" };
const main = document.getElementById("content");
const navigation = document.getElementById("nav");
function setupColorTheme() {
  const button = document.getElementById("theme-toggle");
  if (!button || !document.body || !document.body.setAttribute) return;
  let theme = "light";
  try { theme = localStorage.getItem("rakurs_theme") === "dark" ? "dark" : "light"; }
  catch (_error) { /* Private browsing can disable local storage. */ }
  function apply(value) {
    document.body.setAttribute("data-theme", value);
    button.textContent = value === "light" ? "☾ Темна тема" : "☀ Світла тема";
    button.setAttribute("aria-pressed", value === "dark" ? "true" : "false");
  }
  apply(theme);
  button.addEventListener("click", () => {
    theme = theme === "light" ? "dark" : "light"; apply(theme);
    try { localStorage.setItem("rakurs_theme", theme); } catch (_error) {}
  });
}
setupColorTheme();
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
    // Reveal the server snapshot only on the explicit failure path.
    main.querySelector(".rakurs-startup")?.remove();
    const fallback=main.querySelector(".rakurs-server-fallback");
    if(fallback)fallback.hidden=false;
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
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("uk-UA", {maximumFractionDigits: 4}) : "—";
}
function createEmpty(title, detail) {
  const box = element("div", "empty");
  add(box, element("strong", "", title), element("span", "", detail));
  return box;
}
function section(title, caption) {
  const row = element("div", "section-header product-section-heading");
  const copy=element("div","product-section-copy");
  add(copy,element("h2","",title),element("span","subtle",caption||""));
  row.appendChild(copy);
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
  const scoped = /^\/(latest|history(?:\/range)?|modbus-fields|energy|bms\/cells)(\?|$)/.test(path);
  const suffix = scoped && state.selectedDeviceKey && !/[?&]device=/.test(path) ?
    (path.includes("?") ? "&" : "?") +
    "device=" + encodeURIComponent(state.selectedDeviceKey) : "";
  const url = base + path + suffix;
  const controller=typeof AbortController==="function"?new AbortController():null;
  const timer=controller&&typeof window.setTimeout==="function"?window.setTimeout(()=>controller.abort(),15000):null;
  try {
  const response = await fetch(url, {cache:"no-store",credentials:"same-origin",signal:controller?.signal,
    method:options.body ? "POST" : "GET",
    headers:options.body ? {"Content-Type":"application/json",
      "X-CSRF-Token":state.csrf || ""} : {},
    body:options.body ? JSON.stringify(options.body) : undefined});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "Помилка API RAKURS (" + response.status + ")");
  }
  return await response.json();
  } catch(error){if(error.name==="AbortError")throw Error("Час очікування API вичерпано. Повторіть запит.");throw error;}
  finally{if(timer!==null)window.clearTimeout(timer);}
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
  const search=element("input","nav-search");
  search.type="search";
  search.placeholder="⌕ Знайти розділ…";
  search.setAttribute("aria-label","Пошук розділу Rakurs");
  search.value=state.navQuery;
  navigation.appendChild(search);
  const sections = [
    ["МОНІТОРИНГ", ["overview", "parallel", "pv", "battery", "grid", "load", "inverter"]],
    ["АНАЛІТИКА", ["trends", "history", "archive", "events"]],
    ["НАЛАШТУВАННЯ", ["modbus", "registers", "settings", "system"]]
  ];
  const groups=[];
  for (const [label, names] of sections) {
    const header=element("div","nav-group-label",label);
    navigation.appendChild(header);
    const links=[];
    for (const name of names) {
      const view=views.find(item=>item[0]===name);
      const item=element("a",state.route===name?"active":"");
      item.href="#/"+name;
      if(state.route===name)item.setAttribute("aria-current","page");
      item.title=view[1];
      add(item,element("span","symbol",view[2]),element("span","",view[1]));
      navigation.appendChild(item);
      links.push({item,name,label:view[1]});
    }
    groups.push({header,links});
  }
  const nothing=element("p","nav-empty","Нічого не знайдено. Спробуйте інший запит.");
  navigation.appendChild(nothing);
  function filter(){
    const query=state.navQuery.trim().toLocaleLowerCase("uk");
    let visible=0;
    for(const group of groups){
      let found=0;
      for(const link of group.links){
        const match=!query||(link.label+" "+link.name).toLocaleLowerCase("uk").includes(query);
        link.item.hidden=!match;
        if(match){found++;visible++;}
      }
      group.header.hidden=found===0;
    }
    nothing.hidden=visible>0;
  }
  search.addEventListener("input",()=>{state.navQuery=search.value;filter();});
  search.addEventListener("keydown",event=>{
    if(event.key==="Escape"){
      state.navQuery="";search.value="";filter();
      if(typeof search.blur==="function")search.blur();
    }
  });
  filter();
}
function updateConnection(connected) {
  const node = document.getElementById("connection");
  node.className = "connection " + (connected ? "ok" : "error");
  node.title = connected ? "Працює база MySQL, а не обов’язково зв’язок із Deye" : "Немає підтвердження з’єднання з MySQL";
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
    message("Очікування показників Deye",
      "Поки немає збережених показників. «—» означає відсутність значення, не нуль.");
  }
}
function reading(values, key) {
  const item = values[key];
  return item && item.value_decimal !== null && item.value_decimal !== undefined && item.value_decimal !== "" ?
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
  const root = panel("Як рухалася енергія", "Архівний зріз · дата вимірювання зазначена на картках");
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
  add(diagram, solar,
    arrow(pv === 0 ? "Без генерації" : "Генерація", pv === 0 ? "—" : "→", "solar"), center,
    arrow(load === 0 ? "Без споживання" : "Споживання", load === 0 ? "—" : "→", "load"), consumer,
    utility,
    arrow(grid === 0 ? "Без обміну" : grid > 0 ? "Імпорт" : "Експорт",
      grid === 0 ? "—" : grid > 0 ? "→" : "←", "grid"),
    element("div", "energy-spacer"),
    arrow(battery === 0 ? "Без обміну" : battery > 0 ? "Розряд" : "Заряд",
      battery === 0 ? "—" : battery > 0 ? "←" : "→", "battery"),
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
  if (record && record.observed_at_utc) {
    cell.appendChild(element("time","subtle metric-timestamp",
      "Запис: "+time(record.observed_at_utc)));
  }
  return cell;
}
function panel(title, description) {
  const p = element("section", "panel");
  if (title) p.appendChild(element("h2", "", title));
  if (description) p.appendChild(element("p", "", description));
  return p;
}
function current(cycle) { return cycle === state.cycle; }
function overviewHero() {
  const intro=element("section","dashboard-intro dashboard-hero");
  const layout=element("div","hero-layout"),copy=element("div","hero-copy");
  add(copy,element("div","eyebrow","RAKURS · ЦЕНТР ЕНЕРГОСИСТЕМИ"),
    element("h2","","Енергія під вашим контролем"),
    element("p","","Генерація, батарея, мережа й два Deye — на одному екрані. Тут показані лише збережені дані MySQL."));
  const tags=element("div","hero-tags");
  add(tags,element("span","hero-tag","● Збережені вимірювання MySQL"),
    element("span","hero-tag pending","◷ Фізичне опитування не підтверджено"));
  copy.appendChild(tags);
  const art=element("div","hero-visual");
  art.setAttribute("aria-hidden","true");
  add(art,element("div","hero-sun","☀"),
    element("div","hero-device one","⧉ Deye №1"),
    element("div","hero-device two","⧉ Deye №2"));
  add(layout,copy,art);intro.appendChild(layout);
  return intro;
}
function overviewInsights(values) {
  const container=element("section","insight-strip");
  container.setAttribute("aria-label","Джерело та актуальність показників");
  const validDates=Object.values(values||{}).map(item=>Date.parse(item?.observed_at_utc||""))
    .filter(value=>Number.isFinite(value));
  const latest=validDates.length?new Date(Math.max(...validDates)).toISOString():null;
  for(const [icon,label,value] of [
    ["▣","Джерело даних","Збережений архів MySQL"],
    ["◷","Час останнього запису",latest?time(latest):"Записів ще немає"],
    ["⌁","Опитування обладнання","Не підтверджено"]
  ]) {
    const item=element("div","insight-item"),copy=element("div","insight-copy");
    add(copy,element("small","",label),element("strong","",value));
    add(item,element("span","insight-icon",icon),copy);
    container.appendChild(item);
  }
  return container;
}
function overviewQuickLinks() {
  const links=element("nav","quick-actions");
  links.setAttribute("aria-label","Швидкі розділи Rakurs");
  for(const [route,icon,label,description] of [
    ["parallel","⧉","Два інвертори","Перегляд Deye №1 та Deye №2"],
    ["history","◷","Історія","Архів вимірювань за період"],
    ["registers","▥","Регістри Deye","Адреси V105.3 і кодового профілю"]
  ]) {
    const a=element("a","quick-link");a.href="#/"+route;
    const copy=element("span","quick-text");
    add(copy,element("b","",label),element("small","",description));
    add(a,element("span","quick-visual",icon),copy);links.appendChild(a);
  }
  return links;
}

// Personal dashboard settings are browser-local presentation preferences, never telemetry.
const dashboardMetrics = [
  ["pv_power","Генерація сонця","☀","solar","Потужність фотоелектричних панелей"],
  ["load_power","Навантаження","⌂","load","Споживання"],
  ["battery_soc","Заряд акумулятора","▣","battery","Рівень заряду"],
  ["grid_power","Мережа","⌁","grid","Обмін із мережею"],
  ["battery_power","Потужність АКБ","↗","battery","Потужність накопичувача"],
  ["inverter_temperature","Температура інвертора","♨","load","Температура"],
  ["pv_energy_today","Вироблено за добу","☀","solar","Добовий підсумок із дати запису"],
  ["load_energy_today","Спожито за добу","⌂","load","Добовий підсумок із дати запису"]
];
const dashboardDefaults=["pv_power","load_power","battery_soc","grid_power"];
const dashboardPreferenceKey="rakurs.dashboard.metrics.v1";
function dashboardSavedKeys(){
  let raw="";
  try {raw=localStorage.getItem(dashboardPreferenceKey)||"";}catch(_error){}
  const valid=new Set(dashboardMetrics.map(metric=>metric[0]));
  const keys=raw.split(",").filter((key,index,array)=>
    valid.has(key)&&array.indexOf(key)===index);
  return keys.length?keys:dashboardDefaults.slice();
}
function dashboardSaveKeys(keys){
  try {localStorage.setItem(dashboardPreferenceKey,keys.join(","));}catch(_error){}
}
function dashboardConfigurator(deck,values){
  let selected=dashboardSavedKeys();
  const wrapper=element("div","dashboard-preferences");
  const toolbar=element("div","dashboard-toolbar");
  const description=element("div","dashboard-toolbar-copy");
  add(description,element("strong","","Моя головна панель"),
    element("span","", "Вибирайте важливі показники — налаштування залишаться у цьому браузері."));
  const open=element("button","dashboard-config-button","⚙ Налаштувати картки");
  open.type="button";
  open.setAttribute("aria-expanded","false");
  open.setAttribute("aria-controls","dashboard-options");
  add(toolbar,description,open);
  wrapper.appendChild(toolbar);
  const options=element("section","dashboard-options");
  options.id="dashboard-options";
  options.hidden=true;
  options.setAttribute("aria-label","Налаштування головних показників");
  const top=element("div","dashboard-options-top");
  add(top,element("strong","","Оберіть та впорядкуйте показники"),
    element("p","","Прапорець вмикає картку, стрілки змінюють її порядок. Це не впливає на MySQL."));
  options.appendChild(top);
  const presets=element("div","dashboard-presets");
  const defaultButton=element("button","dashboard-preset","Основні 4");
  const allButton=element("button","dashboard-preset","Усі 8");
  const rowList=element("div","dashboard-option-list");
  const foot=element("div","dashboard-option-footer");
  const status=element("span","dashboard-config-status","");
  add(presets,defaultButton,allButton);
  add(options,presets,rowList);
  add(foot,status,element("span","","Збережено локально · не змінює показників"));
  options.appendChild(foot);
  wrapper.appendChild(options);
  function drawCards(){
    clear(deck);
    for(const key of selected){
      const item=dashboardMetrics.find(metric=>metric[0]===key);
      if(!item)continue;
      deck.appendChild(metricCard(item[1],item[2],key,values,item[3],item[4]));
    }
    status.textContent=selected.length+" із 8 показників на головній";
  }
  function choose(keys){
    selected=keys.slice();
    dashboardSaveKeys(selected);
    drawCards();
    drawOptions();
  }
  function drawOptions(){
    clear(rowList);
    for(const metric of dashboardMetrics){
      const [key,label,icon]=metric;
      const position=selected.indexOf(key);
      const row=element("div","dashboard-option-row");
      const caption=element("label","dashboard-option-label");
      const check=element("input","dashboard-check");
      check.type="checkbox";check.checked=position>=0;check.value=key;
      check.setAttribute("aria-label","Показувати "+label);
      check.addEventListener("change",()=>{
        if(check.checked){
          if(!selected.includes(key))choose([...selected,key]);
        }else if(selected.length>1){
          choose(selected.filter(item=>item!==key));
        }else{
          check.checked=true;
          status.textContent="Має залишитися хоча б одна картка";
        }
      });
      add(caption,check,element("span","",icon+" "+label));
      row.appendChild(caption);
      const arrows=element("div","dashboard-option-move");
      for(const [step,symbol,action] of [[-1,"↑","вище"],[1,"↓","нижче"]]){
        const button=element("button","dashboard-order-button",symbol);
        button.type="button";
        button.disabled=position<0||position+step<0||position+step>=selected.length;
        button.title="Перемістити "+label+" "+action;
        button.setAttribute("aria-label",button.title);
        button.addEventListener("click",()=>{
          const next=selected.slice(),i=next.indexOf(key),j=i+step;
          if(i<0||j<0||j>=next.length)return;
          [next[i],next[j]]=[next[j],next[i]];
          choose(next);
        });
        arrows.appendChild(button);
      }
      row.appendChild(arrows);
      rowList.appendChild(row);
    }
  }
  defaultButton.type="button";allButton.type="button";
  defaultButton.addEventListener("click",()=>choose(dashboardDefaults));
  allButton.addEventListener("click",()=>choose(dashboardMetrics.map(metric=>metric[0])));
  open.addEventListener("click",()=>{
    options.hidden=!options.hidden;
    open.setAttribute("aria-expanded",String(!options.hidden));
    open.textContent=options.hidden?"⚙ Налаштувати картки":"✓ Завершити налаштування";
  });
  drawCards();
  drawOptions();
  return wrapper;
}

async function renderOverview(cycle, primedValues = null, preparedDevices = null) {
  addStatusMessage();
  const v7 = Boolean(window.RakursV7 && window.RakursV7.enabled());
  const keys = ["pv_power", "load_power", "grid_power", "battery_soc",
    "battery_power", "inverter_temperature", "pv_energy_today", "load_energy_today"];
  let values = v7 && primedValues === null ? {} : primedValues;
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

  const boardHost = element("div",v7?"v7-host":"energy-board-host");
  main.appendChild(boardHost);
  const twinPromise = preparedDevices !== null ? Promise.resolve(preparedDevices) :
    fetchParallelDevices(cycle).catch(() => null);
  let boardDevices = [];
  function showBoard(devices,loading) {
    if (!current(cycle)) return;
    if (v7) {
      window.RakursV7.render(boardHost,{
        devices:devices||[],fallbackValues:values,
        selectedDeviceKey:state.selectedDeviceKey,selectedSlotNo:state.selectedSlotNo,
        status:state.status,loading,
        historyLoader:(device,key,frame)=>api("/parallel/history?device="+
          encodeURIComponent(device)+"&key="+encodeURIComponent(key)+
          "&hours="+encodeURIComponent(frame)+"&anchor=latest&resolution=auto"),
        frameLoader:at=>api("/parallel/frame?at="+encodeURIComponent(at)),
        historyRangeLoader:(device,key,start,end)=>api("/history/range?device="+encodeURIComponent(device)+
          "&key="+encodeURIComponent(key)+"&start="+encodeURIComponent(start)+"&end="+encodeURIComponent(end)+"&resolution=auto")
      });
      return;
    }
    if (!window.RakursEnergyBoard || typeof window.RakursEnergyBoard.render !== "function") {
      clear(boardHost);
      boardHost.appendChild(createEmpty("Енергетична схема поки недоступна",
        "Збережені показники доступні нижче. Оновіть сторінку."));
      return;
    }
    window.RakursEnergyBoard.render(boardHost,{
      document, devices:devices||[], fallbackValues:values,
      selectedDeviceKey:state.selectedDeviceKey, selectedSlotNo:state.selectedSlotNo,
      mode:state.energyBoardMode||"system", status:state.status, loading,
      onMode:(mode)=>{
        state.energyBoardMode=mode;
        if (mode!=="system") {
          const chosen=(boardDevices||[]).find(d=>String(d.slot_no)===mode);
          if (chosen) {
            state.selectedDeviceKey=chosen.device_key;
            state.selectedSlotNo=chosen.slot_no;
            try {localStorage.setItem("rakurs.selectedDeye",chosen.device_key);}catch(_error){}
          }
        }
        render();
      }
    });
  }
  if (!v7 || preparedDevices === null) showBoard([],true);
  const resolvedDevices=await twinPromise;
  if(!current(cycle))return;
  boardDevices=resolvedDevices||[];
  showBoard(boardDevices,false);
  if (v7) return;
  main.appendChild(overviewInsights(values));
  section("Ключові показники · Deye №" + state.selectedSlotNo,
    "Збережені вимірювання обраного інвертора · не сума двох пристроїв");
  const deck = element("div", "cards cards-main");
  const batteryPower = reading(values, "battery_power");
  main.appendChild(dashboardConfigurator(deck,values));
  main.appendChild(deck);
  main.appendChild(overviewQuickLinks());
  await renderTwinSummary(cycle,boardDevices);
  if (!current(cycle)) return;

  section("На дату останнього запису",
    "Добові підсумки за дату вимірювання, не обов’язково за сьогодні");
  const extras = element("div", "details-grid");
  extras.appendChild(detailMetric("Вироблено за добу", "pv_energy_today", values, "☀"));
  extras.appendChild(detailMetric("Спожито за добу", "load_energy_today", values, "⌂"));
  extras.appendChild(detailMetric("Потужність АКБ", "battery_power", values,
    batteryPower !== null && batteryPower < 0 ? "↙" : "↗"));
  extras.appendChild(detailMetric("Температура інвертора", "inverter_temperature", values, "♨"));
  main.appendChild(extras);

  await renderTrendDeck(cycle, 24, true);
  if (!current(cycle)) return;
  const footer = element("div", "overview-footer");
  const statusText = state.status ?
    (state.status.displayed_signals ||
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
    btn.addEventListener("click", () => selectHours(value));
    ranges.appendChild(btn);
    return btn;
  });
  heading.appendChild(element("span","subtle","Deye №"+state.selectedSlotNo));
  heading.appendChild(ranges);
  root.appendChild(heading);
  const grid = element("div", "trend-grid");
  root.appendChild(grid);
  const compactEmpty = element("div", "trend-empty-summary");
  compactEmpty.hidden = true;
  compactEmpty.setAttribute("aria-live","polite");
  root.appendChild(compactEmpty);
  const series = [
    ["pv_power", "Сонячна генерація", "кВт", "solar"],
    ["load_power", "Навантаження", "кВт", "load"],
    ["battery_soc", "Заряд акумулятора", "%", "battery"],
    ["grid_power", "Обмін із мережею", "кВт", "network"]
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
  function selectHours(value) {
    hours = value;
    buttons.forEach((button, i) => {
      button.className = frames[i][0] === hours ? "active" : "";
      button.setAttribute("aria-pressed", String(frames[i][0] === hours));
    });
    refresh();
  }
  async function refresh() {
    const index = ++requestIndex;
    bodies.forEach(body => body.replaceChildren(element("p", "subtle", "Завантаження графіка…")));
    const results = await Promise.allSettled(series.map(([key]) =>
      api("/history?key=" + encodeURIComponent(key) + "&hours=" + hours+"&anchor=latest&resolution=auto")));
    if (!current(cycle) || index !== requestIndex) return;
    const allEmpty = results.every(result => result.status === "fulfilled" &&
      !(result.value.points || []).some(point => point.value_decimal !== null &&
        point.value_decimal !== undefined && point.value_decimal !== "" && Number.isFinite(Number(point.value_decimal))));
    const allFailed = results.every(result => result.status === "rejected");
    if (allEmpty || allFailed) {
      grid.hidden = true;
      compactEmpty.hidden = false;
      clear(compactEmpty);
      add(compactEmpty,
        element("div","trend-empty-symbol",allFailed ? "!" : "◷"),
        element("strong","",allFailed ? "Графіки зараз недоступні" : "За обраний період вимірювань немає"),
        element("p","",allFailed ?
          "Дані архіву не змінено. Можна повторити запит." :
          "Це не означає, що архів порожній. Виберіть довший період або відкрийте історію."));
      const actions = element("div","trend-empty-actions");
      if (allFailed) {
        const retry = element("button","trend-empty-button","Повторити");
        retry.type="button";retry.addEventListener("click",refresh);actions.appendChild(retry);
      } else if (hours < 168) {
        const target = hours === 24 ? 72 : 168;
        const next = element("button","trend-empty-button",
          target === 72 ? "Показати 3 доби" : "Показати 7 діб");
        next.type="button";next.addEventListener("click",()=>selectHours(target));
        actions.appendChild(next);
      }
      const link=element("a","trend-history-link","Відкрити історію");
      link.href="#/history";actions.appendChild(link);
      compactEmpty.appendChild(actions);
      return;
    }
    grid.hidden=false;
    compactEmpty.hidden=true;
    clear(compactEmpty);
    results.forEach((result, i) => {
      const body = bodies[i];
      clear(body);
      if (result.status !== "fulfilled") {
        body.appendChild(createEmpty("Дані недоступні", "Не вдалося прочитати історію MySQL."));
        return;
      }
      const points = result.value.points || [];
      const numeric = points.filter(p => p.value_decimal != null && p.value_decimal !== "" && Number.isFinite(Number(p.value_decimal)));
      if (!numeric.length) {
        body.appendChild(createEmpty("Немає вимірювань", "За вибраний період записів немає."));
        return;
      }
      const currentValue = numeric[numeric.length - 1].value_decimal;
      body.appendChild(element("strong", "trend-latest", pretty(Number(currentValue)) +
        " " + series[i][2]));
      body.appendChild(interactiveChart(points, series[i][2]));
      body.appendChild(element("p", "subtle", (result.value.resolution==="extrema"?"Деталізація: "+points.length+" із "+result.value.sample_count+" · ":"") + points.length +
        " точок · " + time(points[0].observed_at_utc) +
        " — " + time(points[points.length - 1].observed_at_utc)));
    });
  }
  await refresh();
}
async function renderTrends(cycle) {
  addStatusMessage();
  const comparison=renderComparativeAnalytics(cycle);
  await Promise.allSettled([comparison,renderTrendDeck(cycle,72,false)]);
}

// Independent report comparison and observed energy counters, on existing Graphs route.
async function renderComparativeAnalytics(cycle){
  const panelRoot=element("section","product-analytics");
  const head=element("div","product-analytics-head");
  add(head,element("h2","","Порівняння Deye №1 і №2"),element("p","subtle","Спільний часовий інтервал і масштаб; показники спільних вузлів не сумуються."));
  const controls=element("div","product-analytics-controls");
  const selector=element("select","");selector.setAttribute("aria-label","Показник порівняння");
  const keys=[["pv_power","Генерація PV","кВт"],["load_power","Навантаження","кВт"],["battery_power","Потужність АКБ","кВт"],["battery_soc","SOC","%"],["grid_power","Мережа","кВт"],["output_power","Вихід інвертора","кВт"],["inverter_temperature","Температура","°C"],["pv1_power","MPPT 1","кВт"],["pv2_power","MPPT 2","кВт"],["grid_l1_voltage","Напруга L1","В"],["grid_l2_voltage","Напруга L2","В"],["grid_l3_voltage","Напруга L3","В"],["output_l1_current","Струм L1","А"],["output_l2_current","Струм L2","А"],["output_l3_current","Струм L3","А"]];
  for(const [key,label] of keys){const opt=element("option","",label);opt.value=key;selector.appendChild(opt);}
  selector.value="pv_power";
  const span=element("select","");span.setAttribute("aria-label","Період порівняння");
  for(const [value,label] of [[24,"24 години"],[72,"3 доби"],[168,"7 діб"]]){const opt=element("option","",label);opt.value=String(value);span.appendChild(opt);}span.value="72";
  add(controls,selector,span);const body=element("div","product-comparison-grid");
  add(panelRoot,head,controls,body);main.appendChild(panelRoot);
  const energy=element("section","product-analytics");
  add(energy,element("h2","","Енергетична аналітика"),element("p","subtle","Різниця накопичувальних лічильників за наявним архівом. Не повний баланс без підтвердженого покриття."));
  const period=element("select","");period.setAttribute("aria-label","Період енергетичного звіту");
  for(const [value,label] of [["day","Доба"],["month","Місяць"],["year","Рік"],["all","Весь час"]]){const opt=element("option","",label);opt.value=value;period.appendChild(opt);}period.value="day";
  const totals=element("div","product-comparison-grid");add(energy,period,totals);main.appendChild(energy);
  let devices;
  try{devices=await fetchParallelDevices(cycle);}catch(_error){}
  if(!current(cycle))return;
  if(!devices?.length){body.appendChild(createEmpty("Немає інверторів","Порівняння недоступне."));return;}
  let requestNo=0,energyNo=0;
  async function compare(){
    const token=++requestNo,metric=keys.find(x=>x[0]===selector.value)||keys[0];
    body.replaceChildren(element("p","subtle","Читання архіву…"));
    const dates=devices.flatMap(d=>Object.values(d.values||{}).map(r=>Date.parse(r.observed_at_utc))).filter(Number.isFinite);
    if(!dates.length){body.replaceChildren(createEmpty("Немає часу запису","Неможливо визначити спільний період."));return;}
    const end=Math.max(...dates),start=end-Number(span.value||72)*3600000;
    const results=await Promise.allSettled(devices.map(d=>api("/history/range?device="+encodeURIComponent(d.device_key)+"&key="+metric[0]+"&start="+encodeURIComponent(new Date(start).toISOString())+"&end="+encodeURIComponent(new Date(end).toISOString().replace(/Z$/,"999Z"))+"&resolution=auto")));
    if(!current(cycle)||token!==requestNo)return;
    clear(body);
    const card=element("article","product-comparison-card product-comparison-full");
    const lines=devices.map((d,i)=>({label:deviceLabel(d,i),points:results[i].status==="fulfilled"?(results[i].value.points||[]):[],failed:results[i].status!=="fulfilled"}));
    const units=[...new Set(lines.flatMap(l=>l.points.map(p=>p.unit).filter(Boolean)))];
    if(units.length>1)card.appendChild(createEmpty("Різні одиниці вимірювання","Порівняння потребує однакових одиниць."));
    else card.appendChild(multiDeviceChart(lines,units[0]||metric[2],start,end));
    if(lines.some(l=>l.failed))card.appendChild(element("p","subtle","Частина архіву недоступна."));
    if(results.some(r=>r.status==="fulfilled"&&r.value.resolution==="extrema"))card.appendChild(element("p","subtle","Показано піки й мінімуми всього періоду."));
    body.appendChild(card);
  }
  const energyLabels={pv_energy_total:"Генерація PV",load_energy_total:"Споживання",grid_import_total:"Імпорт",grid_export_total:"Експорт",battery_charge_total:"Заряд АКБ",battery_discharge_total:"Розряд АКБ"};
  async function report(){
    const token=++energyNo;
    totals.replaceChildren(element("p","subtle","Розрахунок за лічильниками…"));
    const results=await Promise.allSettled(devices.map(d=>api("/energy?device="+encodeURIComponent(d.device_key)+"&period="+encodeURIComponent(period.value))));
    if(!current(cycle)||token!==energyNo)return;
    clear(totals);
    devices.forEach((d,i)=>{
      const card=element("article","product-comparison-card");card.appendChild(element("h3","",deviceLabel(d,i)));
      const r=results[i];
      if(r.status!=="fulfilled"){card.appendChild(createEmpty("Розрахунок недоступний","Повторіть вибір періоду."));totals.appendChild(card);return;}
      const data=r.value,records=data.counters||[];
      if(data.anchor)card.appendChild(element("p","subtle",time(data.start)+" — "+time(data.anchor)));
      for(const [key,label] of Object.entries(energyLabels)){
        const row=records.find(x=>x.key===key),line=element("div","product-energy-row");
        const value=row?.value!=null?number(row.value)+" "+row.unit:"—";
        add(line,element("span","",label),element("strong","",value),element("small","subtle",row?.decreases?"Зменшення лічильника":row?.samples>=2?time(row.first_at)+" — "+time(row.last_at):"Недостатньо записів"));
        card.appendChild(line);
      }
      totals.appendChild(card);
    });
    if(window.RakursV7Model?.pairEnergy){
      const r=window.RakursV7Model.pairEnergy(results.map(x=>x.status==="fulfilled"?x.value:null));
      const summary=element("article","product-comparison-card product-comparison-full product-balance");
      summary.appendChild(element("h3","","Зведення енергосистеми"));
      summary.appendChild(element("p","subtle","Підсумок записів: два PV, спільні вузли враховано один раз."));
      for(const [key,label] of Object.entries(energyLabels)){const item=r.values[key],row=element("div","product-phase-row");add(row,element("span","",label),element("strong","",item?number(item.number)+" кВт·год":"—"));if(item)row.title=time(item.first_at)+" — "+time(item.last_at);summary.appendChild(row);}
      if(r.complete){add(summary,element("p","subtle",time(r.window[0])+" — "+time(r.window[1])),element("strong","","Нерозподілена різниця: "+number(r.residual)+" кВт·год"),element("p","subtle","Різниця не є підтвердженими втратами перетворення."));
        const load=r.values.load_energy_total.number,imp=r.values.grid_import_total.number;
        if(load>0)add(summary,element("p","subtle","Імпорт / споживання: "+number(100*imp/load)+"%. Це співвідношення лічильників, а не автономність: імпорт може заряджати АКБ."));
      }
      else summary.appendChild(element("p","subtle",r.reason));
      totals.appendChild(summary);
    }
  }
  selector.addEventListener("change",compare);span.addEventListener("change",compare);period.addEventListener("change",report);
  await Promise.allSettled([compare(),report()]);
}
function multiDeviceChart(lines,unit,first,last){
  const host=element("div","product-chart-interactive product-overlay-chart"),plot=element("div","product-zoom-plot");
  const legend=element("div","product-chart-legend"),controls=element("div","product-zoom-controls");
  const colors=["#2e9b83","#4788e6","#ba7b26"];
  const series=lines.map((line,i)=>({...line,color:colors[i%colors.length],data:line.points.map(p=>({t:Date.parse(p.observed_at_utc),v:p.value_decimal==null||p.value_decimal===""?NaN:Number(p.value_decimal)})).filter(p=>Number.isFinite(p.t)&&Number.isFinite(p.v)).sort((a,b)=>a.t-b.t)}));
  for(const l of series){const item=element("span","");add(item,element("strong","",l.label),element("small","",l.failed?"Недоступно":l.data.length+" точок"));item.setAttribute("data-series",String(series.indexOf(l)));legend.appendChild(item);}
  let from=first,to=last;
  function draw(){
    const visible=series.map(l=>({...l,data:l.data.filter(p=>p.t>=from&&p.t<=to)}));
    const values=visible.flatMap(l=>l.data.map(p=>p.v)),times=[...new Set(visible.flatMap(l=>l.data.map(p=>p.t)))].sort((a,b)=>a-b);
    if(!times.length){plot.replaceChildren(createEmpty("Немає точок","За вибраний період немає числових записів."));return;}
    const ns="http://www.w3.org/2000/svg",node=(tag,attrs={},txt)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(txt!==undefined)n.textContent=txt;return n;};
    const svg=node("svg",{class:"chart product-timeseries product-multiseries",viewBox:"0 0 640 245",tabindex:0,role:"group","aria-label":"Порівняльний графік Deye. Стрілки вибирають час."});
    const left=55,right=620,top=44,bottom=207,lo=Math.min(...values),hi=Math.max(...values),padding=Math.max((hi-lo)*.08,.01),min=lo-padding,max=hi+padding;
    const x=t=>from===to?(left+right)/2:left+(t-from)/(to-from)*(right-left),y=v=>bottom-(v-min)/(max-min)*(bottom-top);
    for(let n=0;n<4;n++){const yy=top+(bottom-top)*n/3;add(svg,node("line",{x1:left,x2:right,y1:yy,y2:yy,class:"product-gridline"}),node("text",{x:left-6,y:yy+4,"text-anchor":"end",class:"product-axis"},pretty(max-(max-min)*n/3,2)));}
    for(let n=0;n<4;n++){const stamp=from+(to-from)*n/3;svg.appendChild(node("text",{x:x(stamp),y:232,"text-anchor":n===0?"start":n===3?"end":"middle",class:"product-axis"},new Date(stamp).toLocaleString("uk-UA",{timeZone:"Europe/Kyiv",...(to-from>86400000?{day:"2-digit",month:"2-digit"}:{hour:"2-digit",minute:"2-digit"})})));}
    for(const l of visible){const steps=l.data.slice(1).map((p,i)=>p.t-l.data[i].t).filter(n=>n>0).sort((a,b)=>a-b),gap=Math.max(1200000,(steps[Math.floor(steps.length/2)]||0)*3);let d="";
      for(const [i,p] of l.data.entries()){d+=(i&&p.t-l.data[i-1].t<=gap?" L":" M")+x(p.t)+" "+y(p.v);}
      if(d)svg.appendChild(node("path",{d,fill:"none",stroke:l.color,"stroke-width":2.6,class:"product-overlay-line","data-device":l.label}));
      for(const [i,p] of l.data.entries()){
        const before=i===0||p.t-l.data[i-1].t>gap;
        const after=i===l.data.length-1||l.data[i+1].t-p.t>gap;
        if(before&&after)svg.appendChild(node("circle",{cx:x(p.t),cy:y(p.v),r:3.5,fill:l.color}));
      }
    }
    const cursor=node("line",{y1:top,y2:bottom,class:"product-cursor"}),label=node("text",{x:left,y:15,class:"product-chart-label"}),tip=node("text",{x:left,y:31,class:"product-axis"});add(svg,cursor,label,tip);
    let index=times.length-1;
    function select(i){index=Math.max(0,Math.min(times.length-1,i));const t=times[index];cursor.setAttribute("x1",x(t));cursor.setAttribute("x2",x(t));label.textContent=time(new Date(t).toISOString());tip.textContent=visible.map(l=>{const p=l.data.find(p=>p.t===t);return l.label+": "+(p?number(p.v)+" "+unit:"—");}).join(" · ");}
    select(index);
    const choose=e=>{if(!svg.getScreenCTM||!svg.createSVGPoint)return;const matrix=svg.getScreenCTM();if(!matrix)return;const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const target=from+Math.max(0,Math.min(1,(p.matrixTransform(matrix.inverse()).x-left)/(right-left)))*(to-from);let i=0;for(let n=1;n<times.length;n++)if(Math.abs(times[n]-target)<Math.abs(times[i]-target))i=n;select(i);};
    svg.addEventListener("click",choose);svg.addEventListener("pointermove",e=>{if(e.pointerType==="mouse")choose(e);});
    svg.addEventListener("keydown",e=>{if(["Home","End","ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();select(e.key==="Home"?0:e.key==="End"?times.length-1:index+(e.key==="ArrowLeft"?-1:1));}});
    window.RakursV7?.fitChart?.(svg);
    plot.replaceChildren(svg);
  }
  function zoom(f){const width=Math.min(last-first,Math.max(Math.min(last-first,60000),(to-from)*f)),middle=(to+from)/2;from=Math.max(first,Math.min(last-width,middle-width/2));to=from+width;draw();}
  for(const [text,name,action] of [["+","Збільшити масштаб порівняння",()=>zoom(.5)],["−","Зменшити масштаб порівняння",()=>zoom(2)],["Увесь","Скинути масштаб порівняння",()=>{from=first;to=last;draw();}]]){
    const b=element("button","",text);b.type="button";b.setAttribute("aria-label",name);b.addEventListener("click",action);controls.appendChild(b);
  }
  add(host,legend,plot,controls);draw();return host;
}
function technicalBars(label,entries){
  const box=element("div","product-technical-bars");box.appendChild(element("h3","",label));
  const valid=entries.filter(x=>x.r&&x.r.unit===entries.find(v=>v.r)?.r.unit),max=Math.max(1,...valid.map(x=>Math.abs(Number(x.r.value_decimal))));
  const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 320 "+(entries.length*29+8));svg.setAttribute("role","img");svg.setAttribute("aria-label",label);
  function node(tag,attrs,txt){const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(txt!==undefined)n.textContent=txt;svg.appendChild(n);}
  entries.forEach((x,i)=>{const yy=i*29+3,ok=valid.includes(x);node("text",{x:0,y:yy+13,fill:"currentColor","font-size":11},x.name);node("rect",{x:65,y:yy,width:155,height:15,rx:4,fill:"currentColor",opacity:.10});if(ok)node("rect",{x:65,y:yy,width:Math.abs(Number(x.r.value_decimal))/max*155,height:15,rx:4,fill:["#4788e6","#2e9b83","#c58b2d"][i%3]});node("text",{x:315,y:yy+13,"text-anchor":"end",fill:"currentColor","font-size":11},ok?number(x.r.value_decimal)+" "+x.r.unit:"—");});
  box.appendChild(svg);return box;
}
function renderSolarForecast(cycle){
  const box=element("details","product-analytics product-forecast");
  box.appendChild(element("summary","","Прогноз сонячної генерації · 48 годин"));
  const body=element("div","");box.appendChild(body);main.appendChild(box);
  let loaded=false,token=0;
  async function refresh(){
    if(!box.open||loaded)return;loaded=true;const revision=++token;
    body.replaceChildren(element("p","subtle","Завантаження метеопрогнозу…"));
    try{
      const data=await api("/solar-forecast");
      if(!current(cycle)||revision!==token)return;clear(body);
      if(data.state!=="available"){
        const text=data.state==="not_configured"?"Потрібні координати установки, потужність і орієнтація обох PV-масивів та дозвіл на метеосервіс.":data.state==="configuration_error"?"Перевірте конфігурацію прогнозу на сервері.":"Метеодані тимчасово недоступні.";
        body.appendChild(element("p","subtle",data.state==="not_configured"?"Прогноз очікує на налаштування. "+text:text));loaded=false;return;
      }
      const lines=data.arrays.map(a=>({label:"Deye №"+a.slot,points:a.hours.map(r=>({observed_at_utc:r.start_utc,value_decimal:r.kw,unit:"кВт"}))}));
      const times=lines.flatMap(l=>l.points.map(p=>Date.parse(p.observed_at_utc))).filter(Number.isFinite);
      if(!times.length){body.appendChild(createEmpty("Немає прогнозних годин","Повторіть запит."));loaded=false;return;}
      add(body,element("strong","",number(data.estimated_kwh)+" кВт·год · "+data.common_hours+" спільних годин"),
        element("p","subtle","Оцінка потенціалу, не фактична генерація. Не враховує обмеження BMS, затінення, мережу та сніг поза заданим коефіцієнтом."),
        multiDeviceChart(lines,"кВт",Math.min(...times),Math.max(...times)),
        element("p","subtle","Метеодані: Open-Meteo · "+time(data.updated_at_utc)));
      const credit=element("a","","Джерело Open-Meteo");credit.href="https://open-meteo.com/";credit.target="_blank";credit.rel="noopener noreferrer";body.appendChild(credit);
    }catch(_error){if(current(cycle)&&revision===token)body.replaceChildren(createEmpty("Прогноз недоступний","Перевірте підключення метеосервісу."));loaded=false;}
  }
  box.addEventListener("toggle",refresh);
}
function renderBatteryEstimate(box,values){
  const model=window.RakursV7Model;if(!model?.batteryEstimate)return;
  const section=element("details","product-analytics product-battery-estimate");
  section.appendChild(element("summary","","Оцінка залишкового часу та ємності"));
  const body=element("div",""),form=element("div","settings-fields");
  const capacity=settingsField(form,"Корисна ємність при 100% SOC, кВт·год","","number");
  const reserve=settingsField(form,"Мінімальний SOC, %","","number");
  const out=element("div","product-phase-grid");
  try{const pref=JSON.parse(localStorage.getItem("rakurs.battery.estimate")||"null");if(pref){capacity.value=String(pref.capacity);reserve.value=String(pref.reserve);}}catch(_error){}
  function update(){clear(out);const data=model.batteryEstimate(values,capacity.value,reserve.value);
    for(const [label,value] of [["Доступна енергія",data.energyKwh==null?"—":number(data.energyKwh)+" кВт·год"],["За поточного розряду",data.hours==null?"—":number(data.hours)+" год"],["SOH (звіт BMS)",data.soh==null?"—":number(data.soh)+"%"]]){
      const card=element("div","product-phase-card");add(card,element("small","",label),element("strong","",value));out.appendChild(card);}
    if(capacity.value!==""&&reserve.value!==""&&data.energyKwh!==null)try{localStorage.setItem("rakurs.battery.estimate",JSON.stringify({capacity:Number(capacity.value),reserve:Number(reserve.value)}));}catch(_error){}
  }
  for(const input of [capacity,reserve])input.addEventListener("input",update);
  add(body,element("p","subtle","Введіть фактичну корисну ємність спільної АКБ та нижню межу SOC. Оцінка за постійної потужності розряду, без прогнозу майбутнього навантаження."),form,out);
  section.appendChild(body);box.appendChild(section);update();
}
function renderTechnicalSummary(groups,values){
  const type=groups[0],isBattery=type==="battery";
  if(!["pv","grid","load","inverter","battery"].includes(type))return;
  const read=key=>{const r=values[key];return r&&r.value_decimal!=null&&r.value_decimal!==""&&Number.isFinite(Number(r.value_decimal))?r:null;};
  const show=key=>{const r=read(key);return r?number(r.value_decimal)+" "+(r.unit||""):"—";};
  if(type==="pv"){
    const box=element("section","product-analytics product-mppt");box.appendChild(element("h2","","Сонячні трекери MPPT"));
    const cards=element("div","product-comparison-grid");
    for(let n=1;n<=2;n++){const card=element("article","product-phase-card");card.appendChild(element("h3","","MPPT "+n));for(const [key,label] of [["power","Потужність"],["voltage","Напруга"],["current","Струм"]]){const row=element("div","product-phase-row");add(row,element("span","",label),element("strong","",show("pv"+n+"_"+key)));card.appendChild(row);}cards.appendChild(card);}
    add(box,cards);main.appendChild(box);return;
  }
  if(!isBattery){
    const prefix=type==="inverter"?"output":type,box=element("section","product-analytics phase-summary");
    box.appendChild(element("h2","","Три фази"));
    const grid=element("div","product-phase-grid");
    for(let n=1;n<=3;n++){
      const card=element("article","product-phase-card");card.appendChild(element("h3","","L"+n));
      for(const [key,label] of [[prefix+"_l"+n+"_voltage","Напруга"],[prefix+"_l"+n+"_power","Потужність"],[type==="grid"?"ct"+n+"_current":prefix+"_l"+n+"_current","Струм"]]){
        const row=element("div","product-phase-row"),r=read(key);
        add(row,element("span","",label),element("strong","",show(key)));
        if(r?.observed_at_utc)row.title=time(r.observed_at_utc);
        if(type==="grid"&&key.startsWith("ct"))row.appendChild(element("small","subtle","CT"));
        card.appendChild(row);
      }grid.appendChild(card);
    }box.appendChild(grid);
    const voltages=[1,2,3].map(n=>read(prefix+"_l"+n+"_voltage"));
    if(voltages.every(r=>r&&r.unit==="В"&&r.observed_at_utc&&r.observed_at_utc===voltages[0].observed_at_utc)){
      const vals=voltages.map(r=>Number(r.value_decimal)),avg=vals.reduce((s,v)=>s+v,0)/3;
      if(avg>0)box.appendChild(element("p","subtle","Максимальне відхилення фазної напруги від середньої: "+number(100*Math.max(...vals.map(v=>Math.abs(v-avg)))/avg)+"%"));
    }
    for(const [suffix,label] of [["voltage","Напруга, В"],["power","Потужність, кВт"]]){
      const entries=[1,2,3].map(n=>({name:"L"+n,r:read(prefix+"_l"+n+"_"+suffix)}));
      if(entries.some(x=>x.r))box.appendChild(technicalBars(label,entries));
    }
    main.appendChild(box);return;
  }
  const box=element("section","product-analytics bms-summary");
  add(box,element("h2","","Діагностика BMS"),element("p","subtle","Збережені показники модулів, без припущень про їхню фізичну наявність."));
  const overview=element("div","product-phase-grid");
  for(const [key,label] of [["battery_soc","SOC"],["battery_soh","SOH"],["battery_temperature","Температура"],["bms_voltage","Напруга BMS"],["bms_max_charge_current","Макс. зарядний струм"],["bms_max_discharge_current","Макс. розрядний струм"]]){
    const card=element("div","product-phase-card");add(card,element("small","",label),element("strong","",show(key)));
    const value=read(key);
    if((key==="battery_soc"||key==="battery_soh")&&value&&value.unit==="%"&&Number(value.value_decimal)>=0&&Number(value.value_decimal)<=100){
      const gauge=element("div","product-bms-gauge"),fill=element("span","");
      gauge.setAttribute("role","meter");gauge.setAttribute("aria-label",label);gauge.setAttribute("aria-valuemin","0");gauge.setAttribute("aria-valuemax","100");gauge.setAttribute("aria-valuenow",String(Number(value.value_decimal)));
      fill.style.width=Number(value.value_decimal)+"%";gauge.appendChild(fill);card.appendChild(gauge);
    }
    overview.appendChild(card);
  }box.appendChild(overview);
  const modules=element("div","product-bms-modules");
  for(let n=1;n<=20;n++){
    const prefix="bms_"+n+"_",fields=["voltage","current","temperature","soh","min_cell_voltage","max_cell_voltage","alarm","fault"];
    if(!fields.some(k=>read(prefix+k))&&!Object.keys(values).some(k=>k.startsWith(prefix+"cell_")&&read(k)))continue;
    const card=element("details","product-bms-module"),min=read(prefix+"min_cell_voltage"),max=read(prefix+"max_cell_voltage");
    add(card,element("summary","","Модуль "+n+" · "+show(prefix+"voltage")));
    if(min&&max&&min.unit===max.unit&&min.observed_at_utc===max.observed_at_utc&&Number(max.value_decimal)>=Number(min.value_decimal)){
      const scale=min.unit==="В"||min.unit==="V"?1000:min.unit==="мВ"||min.unit==="mV"?1:null;
      if(scale!==null)add(card,element("strong","product-bms-delta","Δ комірок: "+number((Number(max.value_decimal)-Number(min.value_decimal))*scale)+" мВ"));
    }
    for(const [key,label] of [["temperature","Температура"],["current","Струм"],["soh","SOH"],["min_cell_voltage","Мін. комірка"],["max_cell_voltage","Макс. комірка"],["alarm","Код попередження"],["fault","Код помилки"]]){
      const row=element("div","product-phase-row");add(row,element("span","",label),element("strong","",show(prefix+key)));card.appendChild(row);
    }
    const cells=Object.keys(values).filter(k=>new RegExp("^"+prefix+"cell_[1-9][0-9]?_voltage$").test(k)).map(k=>({name:k.slice(prefix.length+5,-8),r:read(k)})).filter(x=>x.r).sort((a,b)=>Number(a.name)-Number(b.name));
    if(cells.length){const latest=cells.map(x=>x.r.observed_at_utc).filter(Boolean).sort().at(-1);const aligned=cells.filter(x=>x.r.observed_at_utc===latest);if(aligned.length)card.appendChild(technicalBars("Напруги комірок · "+time(latest),aligned));if(aligned.length!==cells.length)card.appendChild(element("p","subtle","Різночасові комірки не об’єднуються."));}
    modules.appendChild(card);
  }
  if(!modules.childNodes.length)modules.appendChild(element("p","subtle","Немає числових записів модулів BMS."));
  const moduleList=element("details","product-bms-records");
  moduleList.appendChild(element("summary","","Архівні записи модулів · "+modules.querySelectorAll(".product-bms-module").length));
  moduleList.appendChild(modules);box.appendChild(moduleList);
  const temperatures=Array.from({length:20},(_,i)=>({name:"BMS "+(i+1),r:read("bms_"+(i+1)+"_temperature")})).filter(x=>x.r);
  if(temperatures.length)moduleList.appendChild(technicalBars("Температури модулів",temperatures));
  renderBatteryEstimate(box,values);
  main.appendChild(box);
}
function renderEquipmentHistory(groups,items,known,cycle){
  const preferred={pv:["pv_power","pv1_power","pv2_power","pv1_voltage","pv2_voltage","pv1_current","pv2_current"],battery:["battery_soc","battery_soh","battery_power","battery_temperature","bms_max_charge_current","bms_max_discharge_current"],grid:["grid_power","grid_frequency","grid_l1_voltage","grid_l2_voltage","grid_l3_voltage"],load:["load_power","load_l1_power","load_l2_power","load_l3_power"],inverter:["inverter_temperature","output_power","output_l1_current","output_l2_current","output_l3_current"]};
  const options=(preferred[groups[0]]||[]).map(key=>items.find(i=>i.key===key)).filter(i=>i&&known.has(i.key));if(!options.length)return;
  const panel=element("details","product-analytics product-equipment-history");panel.appendChild(element("summary","","Динаміка параметрів"));
  const select=element("select","");select.setAttribute("aria-label","Показник обладнання");for(const i of options){const opt=element("option","",i.label);opt.value=i.key;select.appendChild(opt);}select.value=options[0].key;
  const period=element("select","");period.setAttribute("aria-label","Період обладнання");for(const [v,label] of [[24,"24 години"],[72,"3 доби"],[168,"7 діб"]]){const opt=element("option","",label);opt.value=String(v);period.appendChild(opt);}period.value="24";
  const controls=element("div","product-analytics-controls"),body=element("div","");add(controls,select,period);add(panel,controls,body);main.appendChild(panel);let revision=0;
  async function refresh(){if(!panel.open)return;const token=++revision;body.replaceChildren(element("p","subtle","Читання історії…"));try{const r=await api("/history?key="+encodeURIComponent(select.value)+"&hours="+period.value+"&anchor=latest&resolution=auto");if(current(cycle)&&token===revision)body.replaceChildren(interactiveChart(r.points||[],r.signal?.unit||options.find(x=>x.key===select.value)?.unit||""));}catch(_error){if(current(cycle)&&token===revision)body.replaceChildren(createEmpty("Історія недоступна","Повторіть запит."));}}
  panel.addEventListener("toggle",refresh);select.addEventListener("change",refresh);period.addEventListener("change",refresh);
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
  // Cell values are retrieved separately, without the generic 500-signal list limit.
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
  if(groups[0]==="battery"){
    try{Object.assign(values,(await api("/bms/cells")).values||{});}
    catch(_error){if(current(cycle))message("Комірки АКБ","Покоміркові записи недоступні.",true);}
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
  if(groups[0]==="inverter")main.appendChild(element("p","subtle","Адреса Modbus у цьому каталозі — збережене поле інвертора. Налаштований Unit ID каналу наведено в розділі «Керування»."));
  const preferred={pv:["pv_power","pv_energy_today"],battery:["battery_soc","battery_power","battery_voltage","battery_current"],grid:["grid_power","grid_voltage","grid_frequency"],load:["load_power","load_energy_today"],inverter:["inverter_temperature","inverter_power","output_power"]};
  const priority=(preferred[groups[0]]||[]).map(key=>items.find(item=>item.key===key)).filter(Boolean);
  const highlight=priority.length?priority:items.filter(item=>values[item.key]?.value_decimal!=null).slice(0,3);
  if(highlight.length){
    const overview=element("section","product-metric-summary");
    for(const metric of highlight){
      const record=values[metric.key],value=record?.value_decimal;
      const tile=element("div","product-metric-highlight");
      add(tile,element("small","",metric.label),
        element("strong","",value==null||value===""?"—":pretty(Number(value),3)+" "+(record.unit||"")),
        element("span","",record?.observed_at_utc?time(record.observed_at_utc):"Немає запису"));
      overview.appendChild(tile);
    }
    main.appendChild(overview);
  }
  renderTechnicalSummary(groups,values);
  if(groups[0]==="pv")renderSolarForecast(cycle);
  renderEquipmentHistory(groups,items,known,cycle);
  main.appendChild(controls);
  const list = element("div", "metric-list product-metric-catalog");
  const entries = [];
  for (const metric of items) {
    const record = values[metric.key];
    const hasValue = record && record.value_decimal != null && record.value_decimal !== "" && Number.isFinite(Number(record.value_decimal));
    const row = element("article", "metric");
    const info = element("div", "metric-info");
    add(info, element("strong", "", metric.label),
      element("small", "", metric.key),
      element("time","subtle metric-timestamp",
        record && record.observed_at_utc ?
          time(record.observed_at_utc) : "Час відсутній"));
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
let chartGradientId=0;
function svgChart(points, unit, domain={}) {
  const ns="http://www.w3.org/2000/svg";
  const node=(tag,attrs={},text)=>{const e=document.createElementNS(ns,tag);
    for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));
    if(text!==undefined)e.textContent=text;return e;};
  const real=(points||[]).map(p=>({t:Date.parse(p.observed_at_utc),v:p.value_decimal==null||p.value_decimal===""?NaN:Number(p.value_decimal)}))
    .filter(p=>Number.isFinite(p.t)&&Number.isFinite(p.v)).sort((a,b)=>a.t-b.t);
  if(!real.length)return createEmpty("Графік без точок","За вибраний період числових значень немає.");
  const svg=node("svg",{class:"chart product-timeseries",viewBox:"0 0 640 226",role:"group",tabindex:0,"aria-label":"Інтерактивний графік. Стрілки ліворуч і праворуч вибирають запис."});
  const left=55,right=620,top=27,bottom=181;
  const first=domain.start??real[0].t,last=domain.end??real.at(-1).t;
  const values=real.map(p=>p.v),min=domain.bounds?.[0]??Math.min(...values),max=domain.bounds?.[1]??Math.max(...values),span=Math.max(max-min,Math.abs(max)*.08,.1);
  // Choose readable intervals while retaining signed flows and flat signals.
  const rough=span/4, decade=10**Math.floor(Math.log10(rough));
  const step=([1,2,2.5,5,10].find(v=>v*decade>=rough)||10)*decade;
  const low=Math.floor((min===max?min-span*.12:min)/step)*step;
  const high=Math.max(low+step,Math.ceil((min===max?max+span*.12:max)/step)*step);
  const x=t=>first===last?(left+right)/2:left+(t-first)/(last-first)*(right-left);
  const y=v=>bottom-(v-low)/(high-low)*(bottom-top);
  const tickCount=Math.round((high-low)/step);
  for(let i=0;i<=tickCount;i++){
    const tick=i===tickCount?low:high-step*i, yy=y(tick);
    svg.appendChild(node("line",{x1:left,x2:right,y1:yy,y2:yy,class:"product-gridline"}));
    svg.appendChild(node("text",{x:left-7,y:yy+4,"text-anchor":"end",class:"product-axis"},pretty(Math.abs(tick)<step/1000?0:tick,Math.max(0,Math.ceil(-Math.log10(step))+1))));
  }
  if(unit)svg.appendChild(node("text",{x:9,y:13,class:"product-axis"},unit));
  const intervals=real.slice(1).map((p,i)=>p.t-real[i].t).filter(v=>v>0).sort((a,b)=>a-b);
  const cadence=intervals.length?intervals[Math.floor((intervals.length-1)/2)]:0;
  const gap=Math.max(1200000,cadence*3);
  const segments=[];let segment=[];
  for(const p of real){if(segment.length&&p.t-segment.at(-1).t>gap){segments.push(segment);segment=[];}segment.push(p);}
  if(segment.length)segments.push(segment);
  const fillId="rakurs-area-"+(++chartGradientId);
  const defs=node("defs"),gradient=node("linearGradient",{id:fillId,x1:0,x2:0,y1:0,y2:1,gradientUnits:"objectBoundingBox"});
  gradient.appendChild(node("stop",{offset:"0%","stop-color":"currentColor","stop-opacity":".20"}));
  gradient.appendChild(node("stop",{offset:"100%","stop-color":"currentColor","stop-opacity":".025"}));
  defs.appendChild(gradient);svg.appendChild(defs);
  for(const s of segments){
    if(s.length===1)svg.appendChild(node("circle",{cx:x(s[0].t),cy:y(s[0].v),r:3.5,class:"product-sample-dot"}));
    else {
      const vertices=s.map(p=>x(p.t).toFixed(2)+","+y(p.v).toFixed(2)).join(" ");
      const baseline=y(Math.min(high,Math.max(low,0)));
      svg.appendChild(node("polygon",{points:vertices+" "+x(s.at(-1).t)+","+baseline+" "+x(s[0].t)+","+baseline,fill:"url(#"+fillId+")","aria-hidden":"true"}));
      svg.appendChild(node("polyline",{points:vertices,class:"product-series-line"}));
    }
  }
  const timeFormat=new Intl.DateTimeFormat("uk-UA",{timeZone:"Europe/Kyiv",...(last-first>86400000?{day:"2-digit",month:"2-digit"}:{hour:"2-digit",minute:"2-digit"})});
  const fractions=first===last?[.5]:[0,1/3,2/3,1];
  for(const f of fractions)svg.appendChild(node("text",{x:left+(right-left)*f,y:205,"text-anchor":f===0?"start":f===1?"end":"middle",class:"product-axis"},timeFormat.format(new Date(first+(last-first)*f))));
  const cursor=node("line",{y1:top,y2:bottom,class:"product-cursor"});
  const dot=node("circle",{r:4,class:"product-cursor-dot"});
  const label=node("text",{x:right,y:14,"text-anchor":"end",class:"product-chart-label"});
  const tip=node("title");
  svg.appendChild(cursor);svg.appendChild(dot);svg.appendChild(label);svg.appendChild(tip);
  let selected=real.length-1;
  function choose(i){selected=Math.max(0,Math.min(real.length-1,i));const p=real[selected],xx=x(p.t),yy=y(p.v);
    cursor.setAttribute("x1",xx);cursor.setAttribute("x2",xx);dot.setAttribute("cx",xx);dot.setAttribute("cy",yy);
    label.textContent=pretty(p.v,2)+(unit?" "+unit:"")+" · "+time(new Date(p.t).toISOString()).slice(0,-3);
    tip.textContent=time(new Date(p.t).toISOString())+" — "+pretty(p.v,3)+" "+(unit||"");
  }
  choose(selected);
  const interact=e=>{if(!svg.getScreenCTM||!svg.createSVGPoint)return;const matrix=svg.getScreenCTM();if(!matrix)return;
    const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;
    const xx=p.matrixTransform(matrix.inverse()).x;
    const target=first+Math.max(0,Math.min(1,(xx-left)/(right-left)))*(last-first);
    let lowIndex=0,highIndex=real.length-1;
    while(lowIndex<highIndex){const mid=(lowIndex+highIndex)>>1;if(real[mid].t<target)lowIndex=mid+1;else highIndex=mid;}
    const i=lowIndex>0&&Math.abs(real[lowIndex-1].t-target)<Math.abs(real[lowIndex].t-target)?lowIndex-1:lowIndex;choose(i);
  };
  svg.addEventListener("click",interact);svg.addEventListener("pointermove",e=>{if(e.pointerType==="mouse")interact(e);});
  svg.addEventListener("keydown",e=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(e.key)){e.preventDefault();choose(e.key==="Home"?0:e.key==="End"?real.length-1:selected+(e.key==="ArrowLeft"?-1:1));}});
  window.RakursV7?.fitChart?.(svg);
  return svg;
}
function interactiveChart(points,unit,domain={}){
  const host=element("div","product-chart-interactive"),plot=element("div","product-zoom-plot"),controls=element("div","product-zoom-controls");
  const valid=(points||[]).filter(p=>Number.isFinite(Date.parse(p.observed_at_utc))&&p.value_decimal!=null&&p.value_decimal!==""&&Number.isFinite(Number(p.value_decimal)));
  if(!valid.length)return svgChart(points,unit,domain);
  const times=valid.map(p=>Date.parse(p.observed_at_utc)),first=domain.start??Math.min(...times),last=domain.end??Math.max(...times);
  let start=first,end=last,pinch=null;
  const status=element("span","subtle");
  function redraw(){const subset=valid.filter(p=>{const t=Date.parse(p.observed_at_utc);return t>=start&&t<=end;});plot.replaceChildren(svgChart(subset,unit,{...domain,start,end}));status.textContent=time(new Date(start).toISOString())+" — "+time(new Date(end).toISOString());}
  function windowAt(center,width){const extent=last-first;const w=Math.min(extent,Math.max(Math.min(60000,extent),width));start=Math.max(first,Math.min(last-w,center-w/2));end=start+w;redraw();}
  function zoom(f){windowAt((start+end)/2,(end-start)*f);}
  for(const [label,name,action] of [["−","Зменшити масштаб",()=>zoom(2)],["+","Збільшити масштаб",()=>zoom(.5)],["‹","Попередній проміжок",()=>windowAt((start+end)/2-(end-start)*.5,end-start)],["›","Наступний проміжок",()=>windowAt((start+end)/2+(end-start)*.5,end-start)],["Увесь","Скинути масштаб",()=>{start=first;end=last;redraw();}]]){
    const btn=element("button","",label);btn.type="button";btn.setAttribute("aria-label",name);btn.addEventListener("click",action);controls.appendChild(btn);
  }
  const distance=e=>Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);
  plot.addEventListener("touchstart",e=>{if(e.touches.length===2){pinch={initial:distance(e),last:distance(e)};}},{passive:true});
  plot.addEventListener("touchmove",e=>{if(pinch&&e.touches.length===2){if(e.cancelable)e.preventDefault();pinch.last=distance(e);}},{passive:false});
  plot.addEventListener("touchend",()=>{if(pinch){const f=pinch.initial/Math.max(1,pinch.last);pinch=null;if(Math.abs(f-1)>.08)zoom(f);}});
  plot.addEventListener("touchcancel",()=>{pinch=null;});
  add(host,plot,controls,status);redraw();return host;
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
        encodeURIComponent(picker.value) + "&hours=" + range.value + "&anchor=latest&resolution=auto");
      if (!current(cycle) || id !== requestId) return;
      clear(plot);
      const points = result.points || [];
      if (!points.length) {
        plot.appendChild(createEmpty("За цей період записів немає",
          "Вибери інший сигнал або довший проміжок часу."));
        return;
      }
      const unit = result.signal && result.signal.unit || points[0]?.unit || "";
      const stats = element("div", "history-summary");
      const numericPoints = points.filter(point => point.value_decimal != null && point.value_decimal !== "" && Number.isFinite(Number(point.value_decimal)));
      const latestNumeric = numericPoints.length ? numericPoints[numericPoints.length - 1] : null;
      add(stats, element("strong", "",
        latestNumeric ? pretty(Number(latestNumeric.value_decimal), 3) + " " + unit : "—"),
        element("span", "", points.length + " записів · " +
          time(points[0].observed_at_utc) + " — " +
          time(points[points.length - 1].observed_at_utc)));
      plot.appendChild(stats);
      plot.appendChild(interactiveChart(points, unit));

      const head = element("div", "history-table-head");
      head.appendChild(element("h3", "", result.resolution==="extrema"?"Вибрані точки графіка":"Останні вимірювання"));
      head.appendChild(element("span", "subtle", "Час Київ · " +
        "MySQL"));
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
  if (key === "deye_inverter_1" || key.endsWith("_parallel_1")) return "Deye №1";
  if (key === "deye_inverter_2" || key.endsWith("_parallel_2")) return "Deye №2";
  return key || "—";
}
function normalizedTwinDevices(raw) {
  // Legacy API versions omitted slot_no; never label both choices Deye №1.
  const devices = Array.isArray(raw) ? raw : [];
  const assigned = new Set();
  return devices.map((device, index) => {
    const reported = Number(device.slot_no);
    let slot = Number.isInteger(reported) && reported > 0 &&
      !assigned.has(reported) ? reported : null;
    if (slot === null) {
      slot = [1, 2].find(candidate => !assigned.has(candidate)) || index + 1;
    }
    assigned.add(slot);
    return {...device, slot_no: slot};
  });
}
function deviceLabel(device, index = 0) {
  const slot = Number(device.slot_no || index + 1);
  return "Deye №" + (Number.isInteger(slot) && slot > 0 ? slot : index + 1);
}
function deviceReading(values, key) {
  const item = values?.[key];
  return item && item.value_decimal !== null && item.value_decimal !== undefined && item.value_decimal !== "" ?
    pretty(Number(item.value_decimal), 2) + " " + (item.unit || "") : "—";
}
function storedDeviceNote(reading) {
  return reading?.observed_at_utc ?
    "Останній запис: " + time(reading.observed_at_utc) : "Записів немає";
}
async function fetchParallelDevices(cycle) {
  const profile = await api("/parallel");
  if (!current(cycle)) return null;
  const devices = normalizedTwinDevices(profile.devices);
  const responses = await Promise.allSettled(devices.map(d =>
    api("/parallel/latest?device=" + encodeURIComponent(d.device_key))));
  if (!current(cycle)) return null;
  return devices.map((d, i) => ({
    ...d, slot_no: d.slot_no,
    values: responses[i].status === "fulfilled" ? (responses[i].value.values || {}) : {},
    telemetry_error: responses[i].status !== "fulfilled",
  }));
}
async function renderTwinSummary(cycle,preloadedDevices=null) {
  let devices=preloadedDevices;
  if (!Array.isArray(devices)) {
    try {devices=await fetchParallelDevices(cycle);} catch(_error){return;}
  }
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
      element("span", "parallel-state", device.telemetry_error?"Недоступно":"Архів"));
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
  const howto=element("a","parallel-open-link","Схема RS485, Unit ID і налаштування TRB145");
  howto.href="#/settings";
  main.appendChild(howto);
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
  const comparison=element("section","product-analytics product-pair-summary");comparison.appendChild(element("h2","","Зведення інверторів"));
  const table=element("table","data-table product-pair-table"),head=element("thead",""),hr=element("tr","");
  for(const label of ["Параметр",...devices.map((d,i)=>deviceLabel(d,i))])hr.appendChild(element("th","",label));head.appendChild(hr);table.appendChild(head);
  const body=element("tbody","");
  for(const [key,label] of [["pv_power","PV"],["load_power","Будинок"],["battery_soc","SOC"],["battery_power","АКБ"],["grid_power","Мережа"],["inverter_temperature","Температура"]]){const row=element("tr","");row.appendChild(element("th","",label));for(const d of devices)row.appendChild(element("td","",deviceReading(d.values,key)));body.appendChild(row);}
  table.appendChild(body);comparison.appendChild(table);main.appendChild(comparison);
  const grid = element("div", "parallel-devices");
  main.appendChild(grid);
  for (const [i, device] of devices.entries()) {
    const query = "?device=" + encodeURIComponent(device.device_key);
    const card = element("article", "parallel-device");
    const heading = element("div", "parallel-device-head");
    add(heading, element("strong", "parallel-device-name", deviceLabel(device, i)),
      element("span", "parallel-state", device.telemetry_error?"Недоступно":"Архів"));
    card.appendChild(heading);
    card.appendChild(element("p", "parallel-device-model",
      device.model || "SUN-20K-SG05LP3-EU-SM2"));
    const metrics = element("div", "parallel-metric-list");
    const fieldsToShow = [
      ["pv_power", "Генерація PV"], ["battery_power", "Потужність АКБ"],
      ["battery_soc", "SOC"], ["load_power", "Навантаження"],
      ["grid_power", "Мережа"], ["inverter_temperature", "Температура"]
    ];
    const stamps=new Set(fieldsToShow.map(([key])=>device.values[key]?.observed_at_utc));
    const sharedTime=stamps.size===1 && device.values.pv_power?.observed_at_utc;
    if(sharedTime)card.appendChild(element("p","subtle",storedDeviceNote(device.values.pv_power)));
    for (const [key,label] of fieldsToShow) {
      const tile = element("div", "parallel-reading");
      add(tile, element("small", "", label),
        element("strong", "", deviceReading(device.values, key)));
      if(!sharedTime)tile.appendChild(element("span","",storedDeviceNote(device.values[key])));
      metrics.appendChild(tile);
    }
    card.appendChild(metrics);
    grid.appendChild(card);
    const result = await Promise.allSettled([
      api("/parallel/history" + query + "&key=pv_power&hours=24&anchor=latest&resolution=auto"),
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
      ["Поля Modbus", fields.status === "fulfilled" ? fieldList.length : "—"],
      ["Сирі регістри", raw.status === "fulfilled" ? raw.value.total : "—"],
      ["Події", events.status === "fulfilled" ? (events.value.events || []).length : "—"]
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
      chart.appendChild(interactiveChart(points, "кВт"));
    }
    card.appendChild(chart);

    const rawPanel = element("details", "parallel-raw-viewer");
    rawPanel.appendChild(element("summary", "", "Регістри Modbus"));
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

    const fieldPanel = element("details", "parallel-raw-viewer parallel-field-viewer");
    fieldPanel.appendChild(element("summary", "",
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

function renderRuleAnalyzer(cycle){
  if(!window.RakursV7Model?.evaluateEpisodes)return;
  const available=state.verified||[],keys=["battery_soc","battery_temperature","inverter_temperature","grid_frequency","grid_l1_voltage","grid_l2_voltage","grid_l3_voltage","load_power","pv_power","battery_power"];
  const metrics=keys.map(k=>available.find(x=>x.signal_key===k)).filter(Boolean);if(!metrics.length)return;
  const box=element("details","product-analytics product-rule-analyzer");box.appendChild(element("summary","","Аналіз перевищень за архівом · Deye №"+state.selectedSlotNo));
  box.appendChild(element("p","subtle","Вкажіть власну межу, затримку та гістерезис. Це аналіз наявних записів, не автоматична аварійна сигналізація і не команда обладнанню."));
  const controls=element("div","settings-fields");
  const metric=settingsField(controls,"Параметр",metrics[0].signal_key,"select",metrics.map(x=>[x.signal_key,x.label_uk+" ("+(x.unit||"—")+")"]));
  const direction=settingsField(controls,"Умова","above","select",[["above","Не менше межі"],["below","Не більше межі"]]);
  const threshold=settingsField(controls,"Межа (в одиницях параметра)","","number");
  const hysteresis=settingsField(controls,"Гістерезис","0","number");
  const delay=settingsField(controls,"Затримка, хв","5","number");
  const gap=settingsField(controls,"Макс. інтервал між записами, хв","30","number");
  const button=element("button","settings-save","Проаналізувати 24 години");button.type="button";
  const result=element("div","");add(box,controls,button,result);main.appendChild(box);
  let token=0;
  button.addEventListener("click",async()=>{
    const revision=++token;button.disabled=true;result.replaceChildren(element("p","subtle","Читання точних записів…"));
    try{
      const rule={direction:direction.value,threshold:threshold.value,hysteresis:hysteresis.value,delaySeconds:Number(delay.value)*60,maxGapSeconds:Number(gap.value)*60};
      if(!threshold.value.trim()||!delay.value.trim()||!gap.value.trim())throw Error("Заповніть межу, затримку й інтервал.");
      const key=metric.value,latest=await api("/latest?keys="+encodeURIComponent(key)),stamp=latest.values?.[key]?.observed_at_utc;
      const end=Date.parse(stamp);if(!Number.isFinite(end))throw Error("Немає часу останнього запису.");
      const query="/history/range?key="+encodeURIComponent(key)+"&start="+encodeURIComponent(new Date(end-86400000).toISOString())+"&end="+encodeURIComponent(new Date(end).toISOString().replace(/Z$/,"999Z"));
      let points=[],before=null;
      for(let page=0;page<20;page++){const r=await api(query+(before?"&before="+encodeURIComponent(before):""));if(!current(cycle)||revision!==token)return;points.unshift(...(r.points||[]));if(!r.truncated){before=null;break;}if(!r.next_before||before===r.next_before)throw Error("Неповний архів");before=r.next_before;}
      if(before)throw Error("Понад 20 000 записів; аналіз за цей період недоступний.");
      if(!points.length)throw Error("За цей період немає вимірювань.");
      const episodes=window.RakursV7Model.evaluateEpisodes(points,rule);
      clear(result);result.appendChild(element("strong","","Підтверджені епізоди: "+episodes.length));
      result.appendChild(element("p","subtle",points.length+" записів · "+time(points[0].observed_at_utc)+" — "+time(points.at(-1).observed_at_utc)));
      for(const episode of episodes.slice(-50).reverse()){
        const row=element("div","product-energy-row"),stateLabel={recovered:"Відновлено",open:"Триває на останньому записі",gap:"Далі немає даних"}[episode.state];
        add(row,element("span","",time(episode.start)),element("strong","",stateLabel),element("small","subtle","Підтверджена тривалість: "+number(episode.durationSeconds/60)+" хв · Екстремум: "+number(episode.extreme)+(episode.end?" · Відновлення: "+time(episode.end):"")));result.appendChild(row);
      }
    }catch(error){if(current(cycle)&&revision===token)result.replaceChildren(createEmpty("Аналіз недоступний",error.message));}
    finally{button.disabled=false;}
  });
}
function renderAlertCenter(cycle){
  const box=element("section","product-analytics product-alert-center");box.appendChild(element("h2","","Оперативна діагностика"));
  const body=element("div","");box.appendChild(body);main.appendChild(box);
  const refresh=element("button","more-button","Оновити стан");refresh.type="button";box.appendChild(refresh);
  let revision=0;
  async function load(){
    const token=++revision;refresh.disabled=true;
    try{
      const data=await api("/alerts");if(!current(cycle)||token!==revision)return;clear(body);
      const recent=data.state==="running",labels={running:"Монітор працює",stale:"Монітор не оновлюється",not_installed:"Фоновий монітор не встановлено",unavailable:"Стан монітора недоступний"};
      add(body,element("strong","",labels[data.state]||"Стан невідомий"),element("p","subtle",data.checked_at_utc?"Перевірено: "+time(data.checked_at_utc):"Немає підтвердженого циклу"));
      const phase={normal:"Норма",pending:"Очікування затримки",active:"Активний епізод",unknown:"Не підтверджено / немає даних"};
      for(const r of data.rules||[]){const row=element("div","product-energy-row");add(row,element("span","",r.label),element("strong","",recent?(phase[r.phase]||"Невідомо"):"Стан застарів"));if(r.start)row.appendChild(element("small","subtle","Початок: "+time(r.start)));body.appendChild(row);}
      const events=element("details","");events.appendChild(element("summary","","Останні переходи стану"));
      const status={active:"Виникнення",recovered:"Відновлення",unknown:"Перервано — стан невідомий"};
      for(const item of (data.events||[]).slice().reverse()){const row=element("div","product-energy-row");add(row,element("span","",item.label),element("strong","",status[item.kind]||item.kind),element("small","subtle",time(item.at)+(item.start?" · початок: "+time(item.start):"")));events.appendChild(row);}
      if(!(data.events||[]).length)events.appendChild(element("p","subtle","Переходів ще немає."));
      body.appendChild(events);
    }catch(_error){if(current(cycle)&&token===revision)body.replaceChildren(createEmpty("Монітор недоступний","Оновіть стан."));}
    finally{refresh.disabled=false;}
  }
  refresh.addEventListener("click",load);load();
}
async function renderEvents(cycle) {
  section("Журнал подій", "Події обладнання та підтверджені операції імпорту MySQL");
  renderAlertCenter(cycle);
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

  const filters=element("div","product-analytics-controls event-filter");
  const severity=element("select","");severity.setAttribute("aria-label","Важливість подій");
  const device=element("select","");device.setAttribute("aria-label","Пристрій подій");
  const period=element("select","");period.setAttribute("aria-label","Період подій");
  function opt(select,value,label){const o=element("option","",label);o.value=value;select.appendChild(o);}
  for(const [v,l] of [["","Усі рівні"],["critical","Аварії"],["warning","Попередження"],["info","Інформація"]])opt(severity,v,l);
  opt(device,"","Усі пристрої");for(const [v,l] of [["","Увесь час"],["24","24 години"],["168","7 діб"],["720","30 діб"]])opt(period,v,l);
  const previous=element("button","more-button","Назад"),next=element("button","more-button","Далі");previous.type=next.type="button";previous.disabled=true;next.disabled=true;
  add(filters,severity,device,period,previous,next);eventPanel.appendChild(filters);
  let offset=0,revision=0;
  async function loadEvents(){const token=++revision;const query=new URLSearchParams({limit:"50",offset:String(offset)});if(severity.value)query.set("severity",severity.value);if(device.value)query.set("device",device.value);if(period.value)query.set("start",new Date(Date.now()-Number(period.value)*3600000).toISOString());
  const events=await api("/events?"+query.toString()).then(value=>({status:"fulfilled",value}),()=>({status:"rejected"}));
  if(!current(cycle)||token!==revision)return;clear(deviceBody);previous.disabled=offset===0;next.disabled=events.status!=="fulfilled"||!events.value.has_more;
  if (events.status !== "fulfilled") {
    deviceCard.childNodes[0].textContent = "—";
    deviceBody.appendChild(createEmpty("Журнал недоступний",
      "Не вдалося прочитати rakurs_device_events."));
  } else {
    const items = events.value.events || [];
    deviceCard.childNodes[0].textContent = String(offset+items.length)+(events.value.has_more?"+":"");
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
  }
  for(const filter of [severity,device,period])filter.addEventListener("change",()=>{offset=0;loadEvents();});previous.addEventListener("click",()=>{offset=Math.max(0,offset-50);loadEvents();});next.addEventListener("click",()=>{offset+=50;loadEvents();});
  const responses=await Promise.allSettled([loadEvents(),api("/batches"),api("/parallel")]);
  if(!current(cycle))return;
  const batches=responses[1];if(responses[2].status==="fulfilled")for(const [i,d] of normalizedTwinDevices(responses[2].value.devices).entries())opt(device,d.device_key,deviceLabel(d,i));
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
      const imported = batch.status === "generated";
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
  renderRuleAnalyzer(cycle);
}

async function renderTable(path, sectionName, headings, fields, cycle) {
  addStatusMessage();
  section(sectionName, "Записи з MySQL rakurs");
  const root = panel();
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
        const displayed = field === "commissioned_at_utc" && !raw ?
          "Не зазначено" : field.endsWith("_utc") ? time(raw) :
          field === "kind" && raw === "inverter" ? "Інвертор" :
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
    root.appendChild(element("p","subtle","Значення адреси у полі регістра належить архівному запису. Налаштований Unit ID каналу наведено в «Керуванні»."));
    const groups = [...new Set(fields.map(f => f.group_name || "Інше"))].sort((a,b)=>a.localeCompare(b,"uk",{numeric:true}));
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
    ["Група", "Параметр", "Регістр", "Значення", "Од.", "Дата й час"].forEach(t =>
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
         item.addresses || "Розрахунковий", value, f.unit || "—",
         f.observed_at_utc ? time(f.observed_at_utc) : "—"].forEach(x =>
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
async function renderRegisterReference(cycle) {
  section("Регістри Deye · звірка V105.3",
    "Офлайн-довідник: документація та профіль, не живе опитування");
  const root = element("section", "panel register-workspace");
  main.appendChild(root);
  const intro = element("div", "register-lead");
  add(intro, element("h2", "", "Що означають адреси регістрів?"),
    element("p", "", "Порівнюємо оригінальний документ MODBUS RTU V105.3 (10.07.2024) та наявний профіль deye_p3. Збіг номера адреси не підтверджує однакову функцію для SUN-20K-SG05LP3-EU-SM2."),
    element("p", "", "У цьому розділі немає Modbus-запитів, команд запису або автоматичного застосування масштабів. Реальні показники шукайте в розділі «Modbus»."));
  root.appendChild(intro);
  try {
    const response = await fetch("/static/register-reference-v1053.json", {
      credentials:"same-origin",cache:"no-store"});
    if (!response.ok) throw new Error("No register reference");
    const data = await response.json();
    if (!current(cycle)) return;
    const totals = element("div", "register-stats");
    for (const [count,label] of [
      [data.counts.document_addresses,"Адреси в документі"],
      [data.counts.profile_addresses,"Адреси профілю"],
      [data.counts.shared_addresses,"Є в обох"],
      [data.counts.union_addresses,"Усього до звірки"]
    ]) {
      const tile = element("div", "register-stat");
      add(tile,element("strong", "", count), element("span", "", label));
      totals.appendChild(tile);
    }
    root.appendChild(totals);
    const toolbar = element("div", "register-toolbar");
    const search=element("input","search-field");
    search.type="search";search.placeholder="Адреса, 0xHEX, назва поля, розділ…";
    search.setAttribute("aria-label","Пошук у звірці регістрів");
    const origin=element("select","");
    origin.setAttribute("aria-label","Джерело адреси");
    for (const [val,label] of [
      ["","Усі адреси"],["both","У двох джерелах"],
      ["document_only","Лише V105.3"],["profile_only","Лише профіль"]
    ]) {const option=element("option","",label);option.value=val;origin.appendChild(option);}
    const zone=element("select","");
    zone.setAttribute("aria-label","Група регістрів");
    for (const [val,label] of [
      ["","Усі групи"],["identity","Ідентифікація"],
      ["settings","Налаштування"],["telemetry","Телеметрія"],
      ["custom","Спеціальні"],["battery","Батарейний пакет"],
      ["language","Мовні"],["profile","Інші адреси профілю"]
    ]) {const option=element("option","",label);option.value=val;zone.appendChild(option);}
    add(toolbar,search,origin,zone);root.appendChild(toolbar);
    const indicator=element("p","register-note");root.appendChild(indicator);
    const wrap=element("div","table-wrap"),table=element("table","data-table");
    const head=element("thead",""),tr=element("tr","");
    for(const label of ["Адреса","Джерело","Розділ V105.3","Назви полів профілю","Од. з профілю"])tr.appendChild(element("th","",label));
    head.appendChild(tr);
    const body=element("tbody","");add(table,head,body);wrap.appendChild(table);root.appendChild(wrap);
    const more=element("button","more-button","Показати ще 100");more.type="button";root.appendChild(more);
    const badges={both:"Два джерела",document_only:"Лише V105.3",profile_only:"Лише deye_p3"};
    let matched=[],displayed=0;
    function draw(start,end){
      for(const entry of matched.slice(start,end)){
        const row=element("tr","register-row");
        add(row,element("td","",entry.address+" ("+entry.hex+")"));
        const cell=element("td",""),badge=element("span","register-badge "+entry.origin,badges[entry.origin]||"—");
        cell.appendChild(badge);row.appendChild(cell);
        add(row,element("td","",entry.zone),
          element("td","",entry.names.length?entry.names.join("; "):"Немає у профілі — див. джерело V105.3"),
          element("td","",entry.units.length?entry.units.join(", "):"—"));
        body.appendChild(row);
      }
      displayed=end;indicator.textContent="Показано "+displayed+" із "+matched.length+
        " адрес · у документі 777 рядків, включно з повторними адресами";
      more.hidden=displayed>=matched.length;
    }
    function filter(){
      const query=search.value.trim().toLocaleLowerCase("uk");
      matched=data.entries.filter(entry=>
        (!origin.value||origin.value===entry.origin)&&(!zone.value||zone.value===entry.kind)&&
        (!query||[entry.address,entry.hex,entry.zone,...entry.names,...entry.units].join(" ").toLocaleLowerCase("uk").includes(query)));
      displayed=0;clear(body);draw(0,Math.min(100,matched.length));
    }
    search.addEventListener("input",filter);
    origin.addEventListener("change",filter);
    zone.addEventListener("change",filter);
    more.addEventListener("click",()=>draw(displayed,Math.min(displayed+100,matched.length)));
    filter();
    root.appendChild(element("p","register-note",
      "Довідник адрес не доводить сумісність конкретної прошивки. Особливо обережно: 60–499 — налаштування; 10000+ — окремий опис батарейних пакетів; 20000+ — мовні ресурси. Жодних записів FC05/06/10/15/16 застосунок не виконує."));
  } catch(_error){
    if(current(cycle))root.appendChild(createEmpty("Довідник поки недоступний",
      "Немає локального файла звірки. Показники MySQL залишаються доступними в інших розділах."));
  }
}

function settingsField(form, label, value, kind = "text", choices = []) {
  const wrap=element("label","settings-field");
  wrap.appendChild(element("span","",label));
  let control;
  if(kind==="select") {
    control=element("select","");
    for(const [v,title] of choices) {
      const item=element("option","",title);
      item.value=String(v);
      if(String(v)===String(value??"")) item.selected=true;
      control.appendChild(item);
    }
    control.value=String(value??"");
  } else {
    control=element("input","");
    control.type=kind;
    if(kind==="checkbox") control.checked=Boolean(value);
    else control.value=value==null?"":String(value);
  }
  wrap.appendChild(control);
  form.appendChild(wrap);
  return control;
}

function renderSharedRs485Guide(cfg) {
  const root=panel("Одна шина RS485 · два Deye · один TRB145",
    "Modbus Unit ID адресує інвертор. Parallel A/B (CAN), BMS і Meter-485 — окремі інтерфейси.");
  root.className+=" settings-panel shared-bus-guide";
  const chain=element("div","shared-bus-chain");
  for(const label of ["Rakurs · один Modbus master","VPN → Teltonika TRB145",
                      "Одна шина RS485 A/B","Deye №1 і Deye №2"])
    chain.appendChild(element("div","shared-bus-node",label));
  root.appendChild(chain);
  const buses=cfg.shared_rs485_buses||[];
  const physical=(cfg.devices||[]).filter(d=>d.member_kind==="physical" &&
    [1,2].includes(Number(d.slot_no)));
  root.appendChild(element("h3","","Підключення з конфігурації MySQL"));
  if(!physical.length) root.appendChild(element("p","parallel-warning",
    "Реальні інвертори ще не підтверджені. Історичні записи MySQL не підтверджують зв'язок з RS485."));
  const cards=element("div","shared-bus-inverters");
  for(const slot of [1,2]){
    const d=physical.find(item=>Number(item.slot_no)===slot);
    const card=element("div","shared-bus-inverter");
    card.appendChild(element("strong","","Deye №"+slot));
    card.appendChild(element("span","",d?(d.display_name||d.device_key):
      "Фізичний інвертор не зареєстрований"));
    card.appendChild(element("span","",
      "Modbus ID: "+(d?.slave_id??"не вказано")+" · "+(d?.parallel_role||"роль невідома")));
    card.appendChild(element("small","",d?.member_verified && d?.transport_verified &&
      d?.verified_connection ? "Конфігурацію каналу підтверджено" :
      "Фізичний канал ще не підтверджений"));
    cards.appendChild(card);
  }
  root.appendChild(cards);
  for(const bus of buses){
    const card=element("div","shared-bus-result");
    card.appendChild(element("strong","",
      (bus.gateway_ip||"Шлюз не вказано")+":"+(bus.gateway_port||"—")+
      " · "+(bus.transports.join(", ")||"Режим не налаштовано")));
    card.appendChild(element("span","",
      "Слоти: "+(bus.slots.join(", ")||"—")+
      " · Modbus ID: "+(bus.slave_ids.join(", ")||"—")));
    card.appendChild(element("small",bus.ready?"shared-bus-valid":"shared-bus-alert",
      bus.ready ? "Записи каналу підтверджені; фізичний тест потрібен окремо" :
      bus.issues.join("; ")));
    root.appendChild(card);
  }
  const rules=element("ul","shared-bus-rules");
  for(const rule of [
    "Два кабелі моніторингового Modbus RS485 з'єднуються паралельно на спільній шині A/B; Parallel A/B (CAN), BMS і Meter-485 не використовувати для цього.",
    "Для спільного TRB145 задайте однакові IP-адресу, TCP-порт і транспорт, але різні Modbus Unit ID. Адреси 01/02 — лише приклад до звірки з LCD Modbus SN.",
    "Режим Modbus TCP шлюзу: Unit ID у заголовку MBAP, а на TRB145 перевірити «Server ID obtained from TCP». Для прозорого RTU-over-TCP потрібен цілий RTU-кадр із CRC.",
    "Реальний порт, прошивку, полярність A/B, термінування шини та обидві відповіді FC03 потрібно перевірити перед запуском.",
    "Колектор Rakurs дозволяє тільки FC03/FC04. Ніяких записів FC06/FC16, перемикань Master/Slave або зміни налаштувань батареї.",
    "Карта Developer089 охоплює інші SG05LP3 до 12 кВт, тому для SUN-20K-SG05LP3-EU-SM2 її ще треба перевірити. Не подвоювати спільні SOC, CT і мережеві значення."
  ]) rules.appendChild(element("li","",rule));
  root.appendChild(rules);
  const manual=element("a","parallel-open-link","Офіційний посібник Deye 14–20 кВт");
  manual.href="https://www.deyeinverter.com/deyeinverter/2024/10/08/%E5%82%A8%E8%83%BD%E4%B8%89%E7%9B%B8-sun-14-20k-sg05lp3-eu-sm2.pdf";
  manual.target="_blank"; manual.rel="noopener noreferrer";
  root.appendChild(manual);
  return root;
}

async function renderSettings(cycle) {
  section("Керування інверторами та опитуванням",
    "Налаштування зберігаються в MySQL · лише читання FC03/FC04");
  let cfg;
  try { cfg=await api("/config"); }
  catch(error) {
    if(current(cycle)) message("Налаштування недоступні",error.message,true);
    return;
  }
  if(!current(cycle)) return;
  main.appendChild(renderSharedRs485Guide(cfg));
  if(!cfg.available) {
    main.appendChild(createEmpty("Модуль ще не встановлено",
      cfg.reason || "Потрібна контрольована міграція MySQL."));
    return;
  }
  const admin=state.role==="admin" && Boolean(state.csrf);
  if(!admin) message("Перегляд без редагування",
    "Змінювати інвертори та параметри опитування може лише адміністратор.",false);
  const box=panel("Автоматичне опитування",
    "Вимкнення має дію від наступного циклу агента. Готовність MySQL ≠ доступність інвертора.");
  box.className+=" settings-panel";
  const status=element("p","settings-state",
    cfg.worker_online ? "Агент: відповідає" : "Агент: не запущений");
  box.appendChild(status);
  box.appendChild(element("p","subtle",
    cfg.enabled ? "Увімкнення запитано · "+(cfg.actively_polling?"є робочі цикли":"виконання не підтверджено") :
    "Опитування вимкнено"));
  const pollFields=element("div","settings-fields");
  const enabled=settingsField(pollFields,"Автоматичне опитування",cfg.enabled,"checkbox");
  const interval=settingsField(pollFields,"Інтервал, секунд (15–3600)",
    cfg.interval_seconds,"number");
  box.appendChild(pollFields);
  if(admin) {
    const save=element("button","settings-save","Зберегти режим і інтервал");
    save.type="button";
    const outcome=element("p","subtle","");
    save.addEventListener("click",async()=>{
      save.disabled=true;
      try {
        await api("/config/polling",{body:{
          enabled:enabled.checked,interval_seconds:Number(interval.value)
        }});
        outcome.textContent="Налаштування збережено";
      } catch(error) {outcome.textContent=error.message;}
      finally {save.disabled=false;}
    });
    box.appendChild(save);box.appendChild(outcome);
  } else {enabled.disabled=true;interval.disabled=true;}
  main.appendChild(box);
  section("Реєстр і параметри з'єднання",
    "IP шлюза, порт, Modbus ID і діапазон регістрів кожного інвертора");
  const makeCard=(d,adding=false)=>{
    const root=panel(adding?"Додати інвертор":(d.display_name||d.device_key));
    root.className+=" settings-panel";
    if(!adding) root.appendChild(element("p","subtle",
      "Внутрішній ключ: "+d.device_key+" · "+(d.slot_no?"Deye №"+d.slot_no:"окремий пристрій")));
    root.appendChild(element("p","settings-state",
      d.verified_connection ? "З'єднання підтверджено окремою процедурою" :
      "Канал не підтверджено · опитування заблоковане до введення в експлуатацію"));
    const fields=element("div","settings-fields");
    const inputs={};
    if(adding) {
      inputs.device_key=settingsField(fields,"Унікальний ключ (латиниця)",
        "inverter_new","text");
      inputs.slot_no=settingsField(fields,"Слот паралельної системи","", "select",
        [["","Не входить у пару"],["1","Deye №1"],["2","Deye №2"]]);
    }
    inputs.display_name=settingsField(fields,"Назва",d.display_name||"Новий інвертор");
    inputs.model=settingsField(fields,"Модель",d.model||"SUN-20K-SG05LP3-EU-SM2");
    inputs.serial_number=settingsField(fields,"Серійний номер",d.serial_number||"");
    inputs.transport=settingsField(fields,"Протокол",d.transport||"unconfigured","select",[
      ["unconfigured","Не налаштовано"],["modbus_tcp","Modbus TCP"],["rtu_tcp","Modbus RTU через TCP-шлюз"]]);
    inputs.gateway_ip=settingsField(fields,"IPv4 шлюза",d.gateway_ip||"");
    inputs.gateway_port=settingsField(fields,"TCP порт",d.gateway_port||"", "number");
    inputs.slave_id=settingsField(fields,"Modbus Unit ID / Modbus SN (1–247)",d.slave_id||"", "number");
    inputs.function_code=settingsField(fields,"Функція читання",d.function_code||3,"select",
      [[3,"FC03 Holding Registers"],[4,"FC04 Input Registers"]]);
    inputs.register_address=settingsField(fields,"Початкова адреса регістрів",
      d.register_address??0,"number");
    inputs.register_count=settingsField(fields,"Кількість регістрів за цикл (1–8)",
      d.register_count||1,"number");
    inputs.timeout_ms=settingsField(fields,"Таймаут, мс",
      d.timeout_ms||1500,"number");
    inputs.enabled=settingsField(fields,"Дозволити опитування цього інвертора",
      d.enabled||false,"checkbox");
    root.appendChild(fields);
    root.appendChild(element("p","subtle",
      "Спільний TRB145: однакові IP, TCP-порт і транспорт, різні Modbus Unit ID. "+
      "Зміна параметрів вимагає повторної фізичної перевірки."));
    if(admin) {
      const button=element("button","settings-save",
        adding?"Створити інвертор":"Зберегти інвертор");
      button.type="button";
      const outcome=element("p","subtle","");
      button.addEventListener("click",async()=>{
        const payload={};
        for(const [key,item] of Object.entries(inputs)) {
          if(key==="slot_no") payload.slot_no=item.value===""?null:Number(item.value);
          else if(key==="enabled") payload[key]=Boolean(item.checked);
          else if(["gateway_port","slave_id"].includes(key))
            payload[key]=item.value===""?null:Number(item.value);
          else if(["function_code","register_address","register_count","timeout_ms"].includes(key))
            payload[key]=Number(item.value);
          else payload[key]=item.value;
        }
        button.disabled=true;
        try {
          await api(adding?"/config/inverters":"/config/inverters/"+d.id,{body:payload});
          outcome.textContent="Збережено. Нове підключення потребує окремого підтвердження.";
        } catch(error) {outcome.textContent=error.message;}
        finally {button.disabled=false;}
      });
      root.appendChild(button);root.appendChild(outcome);
    }else {
      for(const input of Object.values(inputs)) input.disabled=true;
    }
    return root;
  };
  for(const device of cfg.devices||[]) main.appendChild(makeCard(device));
  if(admin) main.appendChild(makeCard({},true));
  main.appendChild(element("p","subtle",
    "Налаштування не змінюють інвертор. Фізичне опитування можливе лише після "+
    "підтвердження порту, Modbus ID, окремого агента та дозволеного шлюза."));
}
async function renderArchive(cycle) {
  section("Архів · Deye №"+state.selectedSlotNo,
    "Точний часовий інтервал і експорт лише фактично повернених MySQL записів");
  const root=panel("Пошук в архіві","Діапазон до 31 доби · адаптивний графік · точний CSV");
  root.className+=" history-workspace";
  const controls=element("div","settings-fields");
  const signals=state.verified || [];
  if(!signals.length){
    main.appendChild(createEmpty("Каталог порожній","Немає доступних сигналів для архіву."));
    return;
  }
  const picker=settingsField(controls,"Показник","pv_power","select",
    signals.map(x=>[x.signal_key,x.label_uk+" · "+x.signal_key]));
  // The controls are explicitly Kyiv wall time, regardless of client timezone.
  const kyivFormatter=new Intl.DateTimeFormat("en-GB",{timeZone:"Europe/Kyiv",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"});
  const localValue=date=>{
    const p=Object.fromEntries(kyivFormatter.formatToParts(date).map(x=>[x.type,x.value]));
    return p.year+"-"+p.month+"-"+p.day+"T"+p.hour+":"+p.minute;
  };
  const parseKyiv=(value,upper)=>{
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))return new Date(NaN);
    const wall=Date.parse(value+":00Z");
    if(!Number.isFinite(wall))return new Date(NaN);
    const candidates=[];
    for(let offset=-180;offset<=180;offset+=30){
      const d=new Date(wall-offset*60000);
      if(localValue(d)===value)candidates.push(d.getTime());
    }
    if(!candidates.length)return new Date(NaN);
    // During autumn overlap include both occurrences, without losing samples.
    return new Date(upper?Math.max(...candidates)+59999:Math.min(...candidates));
  };
  const now=new Date();
  const start=settingsField(controls,"Від (час Києва)",
    localValue(new Date(now.getTime()-72*3600*1000)),"datetime-local");
  const end=settingsField(controls,"До (час Києва)",
    localValue(now),"datetime-local");
  root.appendChild(controls);
  const button=element("button","settings-save","Знайти в архіві");
  button.type="button";
  root.appendChild(button);
  const resultArea=element("div","history-records");
  root.appendChild(resultArea);
  main.appendChild(root);
  let request=0;
  async function refresh(){
    const token=++request;
    clear(resultArea);
    resultArea.appendChild(element("p","subtle","Читання архіву MySQL…"));
    try{
      const from=parseKyiv(start.value,false),to=parseKyiv(end.value,true);
      if(Number.isNaN(from.getTime())||Number.isNaN(to.getTime())||
         from>=to||(to-from)>31*86400*1000)
        throw Error("Вкажіть коректний інтервал тривалістю до 31 доби");
      const requestedKey=picker.value,requestedSlot=state.selectedSlotNo;
      const query="/history/range?key="+encodeURIComponent(requestedKey)+
        "&start="+encodeURIComponent(from.toISOString())+
        "&end="+encodeURIComponent(to.toISOString().replace(/Z$/,"999Z"))+
        (state.selectedDeviceKey?"&device="+encodeURIComponent(state.selectedDeviceKey):"");
      const response=await api(query+"&resolution=auto");
      if(!current(cycle)||token!==request)return;
      clear(resultArea);
      const points=response.points||[];
      resultArea.appendChild(element("p","subtle",response.resolution==="extrema"?points.length+" точок графіка з "+response.sample_count+" записів":points.length+" записів"+(response.truncated?" · частковий результат":"")));
      if(!points.length){
        resultArea.appendChild(createEmpty("Вимірювань немає",
          "Виберіть іншу дату або інший показник."));
        return;
      }
      resultArea.appendChild(interactiveChart(points,response.signal?.unit||signals.find(s=>s.signal_key===requestedKey)?.unit||""));
      const exportButton=element("button","settings-save","Експорт CSV");
      exportButton.type="button";
      exportButton.addEventListener("click",async()=>{
        exportButton.disabled=true;exportButton.textContent="Експорт…";
        try{
        let all=[],before=null;
        for(let page=0;page<100;page++){
          const raw=await api(query+(before?"&before="+encodeURIComponent(before):""));
          if(!current(cycle)||token!==request)return;
          all.unshift(...(raw.points||[]));
          if(!raw.truncated){before=null;break;}
          if(!raw.next_before||raw.next_before===before)throw Error("Неможливо продовжити точний експорт");
          before=raw.next_before;
        }
        if(before)throw Error("Понад 100 000 записів. Виберіть коротший період.");
        if(!all.length)throw Error("Немає записів для експорту");
        const csvCell=value=>'"'+String(value??"").replaceAll('"','""')+'"';
        const safeText=value=>/^[=+\-@\t\r\n]/.test(String(value??""))?"'"+value:value;
        const lines=["observed_at_utc,value_decimal,quality"];
        for(const p of all) lines.push([
          csvCell(safeText(p.observed_at_utc)),csvCell(p.value_decimal),csvCell(safeText(p.quality))].join(","));
        const blob=new Blob(["\ufeff"+lines.join("\r\n")],
          {type:"text/csv;charset=utf-8"});
        const url=URL.createObjectURL(blob);
        const link=document.createElement("a");
        link.href=url;link.download="rakurs-"+requestedKey+"-Deye"+
          requestedSlot+".csv";
        document.body.appendChild(link);
        link.click();link.remove();URL.revokeObjectURL(url);
        }catch(error){if(current(cycle)&&token===request)resultArea.appendChild(createEmpty("Експорт не завершено",error.message));}
        finally{exportButton.disabled=false;exportButton.textContent="Експорт CSV";}
      });
      resultArea.appendChild(exportButton);
      const wrap=element("div","table-wrap");
      const table=element("table","data-table");
      const th=element("tr","");
      ["Дата й час","Значення"].forEach(x=>
        th.appendChild(element("th","",x)));
      const head=element("thead","");head.appendChild(th);table.appendChild(head);
      const body=element("tbody","");
      if(points.length>50)resultArea.appendChild(element("p","subtle","У таблиці — останні 50 точок графіка. CSV містить точні записи за вибраний період."));
      for(const p of points.slice(-50).reverse()){
        const row=element("tr","");
        [time(p.observed_at_utc),p.value_decimal==null?"—":number(p.value_decimal)]
          .forEach(x=>row.appendChild(element("td","",x)));
        body.appendChild(row);
      }
      table.appendChild(body);wrap.appendChild(table);resultArea.appendChild(wrap);
    }catch(error){
      if(current(cycle)&&token===request){
        clear(resultArea);
        resultArea.appendChild(createEmpty("Архів не відкрито",error.message));
      }
    }
  }
  button.addEventListener("click",refresh);
  // Prefer the actual latest saved measurement over the current clock.
  try{
    const latest=await api("/latest?keys=pv_power,load_power,battery_power,grid_power,battery_soc");
    if(!current(cycle))return;
    const stamps=Object.values(latest.values||{}).map(r=>Date.parse(r.observed_at_utc)).filter(Number.isFinite);
    if(stamps.length){const anchor=Math.max(...stamps);end.value=localValue(new Date(anchor));start.value=localValue(new Date(anchor-72*3600*1000+60000));}
  }catch(_error){/* manual search remains available */}
  if(current(cycle))await refresh();
}
async function renderSystem(cycle) {
  addStatusMessage();
  section("Конфігурація системи", "Показники та пристрої MySQL");
  const responses=await Promise.allSettled([api("/config"),api("/status"),api("/parallel/health")]);
  if(!current(cycle))return;
  const polling=responses[0].status==="fulfilled"?responses[0].value:null;
  const status=responses[1].status==="fulfilled"?responses[1].value:null;
  const health=responses[2].status==="fulfilled"?responses[2].value:null;
  const diagnostic=element("section","product-analytics product-health");diagnostic.appendChild(element("h2","","Оперативний стан"));
  const healthGrid=element("div","product-comparison-grid");
  const collector=status?.collector;
  for(const [label,value,note] of [["MySQL",status?"Доступна":"Недоступна","Стан API"],["Modbus-агент",collector?.worker_online?"Працює":polling?.worker_online?"Працює":"Не підтверджено",collector?.worker_heartbeat_utc?time(collector.worker_heartbeat_utc):"Немає heartbeat"],["Опитування",collector?.collector_active?"Є успішне читання":polling?.enabled?"Немає підтвердженого читання":"Вимкнено",collector?.last_success_utc?time(collector.last_success_utc):"Немає успішного читання"],["Декодування",collector?.telemetry_decoder_ready?"Готове":"Не підтверджено","Не ототожнюється з доступністю RS485"]]){const card=element("article","product-phase-card");add(card,element("small","",label),element("strong","",value),element("p","subtle",note));healthGrid.appendChild(card);}
  for(const d of health?.devices||[]){const card=element("article","product-phase-card");add(card,element("small","",deviceLabel(d)),element("strong","",d.last_poll?.status||"Немає читань"),element("p","subtle",d.last_poll?.observed_at_utc?time(d.last_poll.observed_at_utc):"Час відсутній"));healthGrid.appendChild(card);}
  diagnostic.appendChild(healthGrid);main.appendChild(diagnostic);
  const pane = panel("Єдина MySQL");
  const info = element("div", "kv");
  [
    ["Сервер", "192.168.5.30"],
    ["База", "rakurs"],
    ["Джерело", "MySQL"],
    ["Пристрої у базі", status ? status.registered_devices : "—"],
    ["Показники у базі", status ? status.displayed_signals : "—"],
    ["Автоматичне опитування", polling?.actively_polling ? "Виконується" : polling?.enabled ? "Увімкнено, агент не підтверджений" : "Вимкнено"]
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
  if(document.body?.setAttribute)document.body.setAttribute("data-route",state.route);
  buildNav();
  const picker=document.getElementById("device-select");
  if(picker && picker.childNodes && picker.childNodes.length){
    picker.value=state.selectedDeviceKey || picker.childNodes[0].value;
  }
  document.getElementById("view-title").textContent = views.find(v => v[0] === state.route)[1];
  let primedValues = null;
  let preparedV7Devices = null;
  let v7PreflightFailed = false;
  const v7Overview = state.route === "overview" &&
    Boolean(window.RakursV7 && window.RakursV7.enabled());
  if(state.route==="overview"&&!v7Overview&&document.body?.getAttribute?.("data-interface")==="rakurs"){
    restoreServerReadings();
    message("Огляд тимчасово недоступний","Збережені серверні показники залишено. Оновіть сторінку.",true);
    return;
  }
  // Full application toolbar becomes compact only for the V7 overview.
  // Other routes and V6 retain their original presentation.
  if (typeof document.body?.classList?.toggle === "function")
    document.body.classList.toggle("rakurs-v7-overview",v7Overview);
  if (v7Overview) {
    try { preparedV7Devices = await fetchParallelDevices(cycle); }
    catch (_error) { v7PreflightFailed = true; }
    if (!current(cycle)) return;
    if (!preparedV7Devices || preparedV7Devices.length === 0 ||
        preparedV7Devices.every(device => device.telemetry_error === true)) {
      v7PreflightFailed = true;
    }
    if (v7PreflightFailed && SSR_PRESENT && !state.selectedDeviceKey) {
      // The server-rendered MySQL readings are trusted more than a failed
      // twin API. Never wipe them or misattribute a single-inverter reading.
      if (window.RakursV7?.disposeAll) window.RakursV7.disposeAll();
      if (typeof document.body?.classList?.remove === "function")
        document.body.classList.remove("rakurs-v7-overview");
      restoreServerReadings();
      message("Огляд тимчасово недоступний",
        "Не вдалося підтвердити читання двох Deye. Збережено серверні показники MySQL.", true);
      return;
    }
    preparedV7Devices = preparedV7Devices || [];
    state.overviewSignature=overviewSignature(preparedV7Devices);
    // V7 consumes only device-scoped parallel endpoints, not legacy /latest.
    primedValues = {};
  }
  if (state.route === "overview" && SSR_PRESENT && !state.selectedDeviceKey &&
      !(window.RakursV7 && window.RakursV7.enabled())) {
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
  // A route change must release V7 archive timers and dialog listeners.
  if (window.RakursV7?.disposeAll) window.RakursV7.disposeAll();
  clear(main);
  switch (state.route) {
    case "overview":
      await renderOverview(cycle, primedValues, preparedV7Devices);
      if (v7Overview && v7PreflightFailed && current(cycle))
        message("Дані двох Deye недоступні",
          "Не вдалося прочитати MySQL через API. Показники не підмінено.", true);
      break;
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
    case "archive": await renderArchive(cycle); break;
    case "modbus": await renderModbus(cycle); break;
    case "registers": await renderRegisterReference(cycle); break;
    case "system": await renderSystem(cycle); break;
    case "settings": await renderSettings(cycle); break;
  }
}
function overviewSignature(devices){return JSON.stringify((devices||[]).map(d=>[d.device_key,d.telemetry_error,Object.entries(d.values||{}).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,v.value_decimal,v.observed_at_utc,v.quality,v.sign_verified,v.register_verified])]));}
async function refreshOverviewInPlace(){
  if(state.autoBusy||state.route!=="overview"||document.hidden||!window.RakursV7?.canAutoRefresh?.())return;
  state.autoBusy=true;
  const cycle=state.cycle;
  try{
    const results=await Promise.allSettled([fetchParallelDevices(cycle),api("/status")]);
    if(!current(cycle))return;
    const devices=results[0].status==="fulfilled"?results[0].value:null;
    const nextStatus=results[1].status==="fulfilled"?results[1].value:null;
    const statusChanged=JSON.stringify([state.status?.collector?.enabled,state.status?.collector_active,state.status?.physical_topology_verified,state.status?.register_semantics_verified])!==JSON.stringify([nextStatus?.collector?.enabled,nextStatus?.collector_active,nextStatus?.physical_topology_verified,nextStatus?.register_semantics_verified]);
    state.status=nextStatus;updateConnection(nextStatus!==null);
    if(!devices?.length||devices.some(d=>d.telemetry_error)||!window.RakursV7.canAutoRefresh())return;
    if(statusChanged||overviewSignature(devices)!==state.overviewSignature){
      // Replace only the overview component; preserve navigation, theme, and scroll.
      const host=main.querySelector(".v7-host");
      if(host){state.overviewSignature=overviewSignature(devices);window.RakursV7.updateDevices(host,devices,state.status);}
    }
  }catch(_error){/* Keep last successfully displayed readings. */}
  finally{state.autoBusy=false;}
}
async function refreshSystemMetadata(){
  const results=await Promise.allSettled([api("/status"),api("/signals"),api("/catalog")]);
  state.status=results[0].status==="fulfilled"?results[0].value:null;
  if(results[1].status==="fulfilled")state.verified=results[1].value.signals||[];
  if(results[2].status==="fulfilled")state.catalog=results[2].value;
  updateConnection(state.status!==null);
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
    mode.textContent = empty ? "Очікування показників" : "Архів MySQL";
    mode.className = "mode-indicator " + (empty ? "mode-pending" : "mode-archive");
    mode.title = empty ? "У базі ще немає вимірювань" :
      "Архівні вимірювання — це не підтвердження зв’язку з інвертором";
  }
  // Styling and data selection use the same operational mode.
  updateConnection(state.status !== null);
  if (results[3].status === "fulfilled") {
    const auth=results[3].value;
    state.role=auth.role || "viewer";state.csrf=auth.csrf || null;
    createLogout(auth);
  }
  const pair=normalizedTwinDevices(
    results[4].status === "fulfilled" ? results[4].value.devices : []);
  try {
    const stored=typeof localStorage !== "undefined" ?
      localStorage.getItem("rakurs.selectedDeye") : null;
    const found=(pair||[]).find(x=>x.device_key===stored);
    if(found){state.selectedDeviceKey=stored;state.selectedSlotNo=found.slot_no;}
  }catch(_error){}
  const choice=document.getElementById("device-select");
  if(choice && (pair||[]).length) {
    clear(choice);
    for(const d of pair) {
      const option=element("option","",deviceLabel(d));
      option.value=d.device_key;
      choice.appendChild(option);
    }
    choice.value=state.selectedDeviceKey || pair[0].device_key;
    choice.hidden=(pair||[]).length<2;
    choice.addEventListener("change",()=>{
      const picked=pair.find(d=>d.device_key===choice.value);
      if(!picked)return;
      state.selectedDeviceKey=picked.device_key;
      state.selectedSlotNo=picked.slot_no;
      state.energyBoardMode=String(picked.slot_no);
      try {if(typeof localStorage!=="undefined")
        localStorage.setItem("rakurs.selectedDeye",picked.device_key);
      } catch(_error) {}
      if(state.route==="overview" && main.querySelector(".v7-host"))return;
      render();
    });
  }
  const refresh=document.getElementById("refresh-data");
  if(refresh){
    refresh.addEventListener("click",async()=>{
      if(refresh.disabled)return;
      refresh.disabled=true;
      refresh.textContent="◌ Оновлення…";
      try{await refreshSystemMetadata();await render();}
      finally{
        refresh.disabled=false;
        refresh.textContent="↻ Оновити";
      }
    });
  }
  await render();
  if(typeof window.setInterval==="function")window.setInterval(refreshOverviewInPlace,30000);
}
window.addEventListener("hashchange", () => { window.scrollTo?.(0,0); return render(); });
boot();
// An installable, network-only shell; never store authenticated telemetry.
if(typeof navigator!=="undefined"&&navigator.serviceWorker&&window.isSecureContext){navigator.serviceWorker.register("/static/rakurs-sw.js",{scope:"/"}).catch(()=>{});}

