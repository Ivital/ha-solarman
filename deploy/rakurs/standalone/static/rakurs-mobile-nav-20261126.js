"use strict";
/* Mobile-only navigation for Rakurs. Hash links only: no API calls, Modbus, or MySQL writes.
   Routes must match the standalone dashboard's views list. */
(function (root) {
  const groups = [
    ["Моніторинг", [
      ["overview", "Огляд", "⌂"],
      ["parallel", "Паралельна система", "⧉"],
      ["pv", "Сонце", "☀"],
      ["battery", "Акумулятор", "▣"],
      ["grid", "Мережа", "⌁"],
      ["load", "Навантаження", "⌂"],
      ["inverter", "Інвертор", "▤"]
    ]],
    ["Аналітика", [
      ["trends", "Графіки", "▥"],
      ["history", "Історія", "◷"],
      ["archive", "Архів", "▦"],
      ["events", "Події", "◇"]
    ]],
    ["Налаштування", [
      ["modbus", "Modbus", "▤"],
      ["registers", "Регістри V105.3", "▥"],
      ["settings", "Керування", "⚙"],
      ["system", "Система", "⚙"]
    ]]
  ];
  const routeMap = new Map(groups.flatMap(group => group[1]).map(item => [item[0], item]));
  const quickRoutes = new Set(["overview","parallel","pv","history"]);
  function routeOf(hash) {
    const key = String(hash || "").replace(/^#\/?/, "").split(/[/?]/)[0];
    return routeMap.has(key) ? key : "overview";
  }
  function init() {
    const doc = root.document;
    const tabs = doc.getElementById("mobile-tabs");
    const more = doc.getElementById("mobile-more");
    const panel = doc.getElementById("mobile-pages");
    const overlay = doc.getElementById("mobile-pages-backdrop");
    const close = doc.getElementById("mobile-pages-close");
    const input = doc.getElementById("mobile-pages-search");
    const list = doc.getElementById("mobile-page-list");
    if (!tabs || !more || !panel || !overlay || !close || !input || !list) return;
    // Guard against a second init on a restored page.
    if (tabs.getAttribute("data-ready") === "true") return;
    tabs.setAttribute("data-ready","true");
    const quick = Array.from(tabs.querySelectorAll("a[data-route]"));
    const menuLinks = [];
    const menuGroups = [];
    for (const [heading, items] of groups) {
      const group = doc.createElement("div");
      group.className = "mobile-menu-group";
      const title = doc.createElement("h3");
      title.className = "mobile-menu-group-title";
      title.textContent = heading;
      group.appendChild(title);
      let links = [];
      for (const [key, label, symbol] of items) {
        const a = doc.createElement("a");
        a.className = "mobile-menu-link";
        a.href = "#/" + key;
        a.dataset.route = key;
        const icon = doc.createElement("span");
        icon.className = "mobile-menu-symbol";
        icon.setAttribute("aria-hidden","true");
        icon.textContent = symbol;
        const name = doc.createElement("span");
        name.className = "mobile-menu-name";
        name.textContent = label;
        a.append(icon,name);
        group.appendChild(a);
        links.push({a,key,label});
        menuLinks.push({a,key,label});
      }
      list.appendChild(group);
      menuGroups.push({group,links});
    }
    const noResults = doc.createElement("p");
    noResults.className = "mobile-menu-empty";
    noResults.textContent = "Нічого не знайдено";
    noResults.hidden = true;
    list.appendChild(noResults);
    let previousFocus = null;
    function setActive() {
      const selected = routeOf(root.location.hash);
      for (const a of quick) {
        const active = a.dataset.route === selected;
        a.classList.toggle("is-active",active);
        if (active) a.setAttribute("aria-current","page");
        else a.removeAttribute("aria-current");
      }
      for (const item of menuLinks) {
        const active = item.key === selected;
        item.a.classList.toggle("is-active",active);
        if (active) item.a.setAttribute("aria-current","page");
        else item.a.removeAttribute("aria-current");
      }
      const other = !quickRoutes.has(selected);
      more.classList.toggle("is-active",other);
      more.setAttribute("aria-label",other ?
        "Розділи, зараз: " + routeMap.get(selected)[1] : "Усі розділи");
    }
    function filter() {
      const query = input.value.trim().toLocaleLowerCase("uk");
      let matches = 0;
      for (const {group,links} of menuGroups) {
        let shown = 0;
        for (const item of links) {
          const visible = !query || (item.label+" "+item.key).toLocaleLowerCase("uk").includes(query);
          item.a.hidden = !visible;
          if (visible) {shown++; matches++;}
        }
        group.hidden = shown===0;
      }
      noResults.hidden = matches>0;
    }
    function toggle(open,restoreFocus=true) {
      const currentlyOpen = !panel.hidden;
      if (open===currentlyOpen) return;
      if (open) previousFocus = doc.activeElement;
      panel.hidden = !open;
      overlay.hidden = !open;
      more.setAttribute("aria-expanded",String(open));
      doc.body.classList.toggle("mobile-menu-open",open);
      if (open) {
        input.value="";
        filter();
        if(typeof input.focus==="function") input.focus();
      } else if (restoreFocus && previousFocus && typeof previousFocus.focus==="function") {
        previousFocus.focus();
      }
    }
    more.addEventListener("click",()=>toggle(panel.hidden));
    close.addEventListener("click",()=>toggle(false));
    overlay.addEventListener("click",()=>toggle(false));
    input.addEventListener("input",filter);
    for (const {a,key} of menuLinks) {
      a.addEventListener("click",()=>{
        toggle(false,false);
        if (routeOf(root.location.hash)===key && typeof root.scrollTo==="function")
          root.scrollTo(0,0);
      });
    }
    for(const a of quick) {
      a.addEventListener("click",()=>{
        toggle(false,false);
        if (routeOf(root.location.hash)===a.dataset.route &&
            typeof root.scrollTo==="function") root.scrollTo(0,0);
      });
    }
    doc.addEventListener("keydown",event=>{
      if(panel.hidden) return;
      if(event.key==="Escape"){
        event.preventDefault();
        toggle(false);
      } else if(event.key==="Tab"){
        // Simple focus loop inside the modal sheet for keyboard accessibility.
        const nodes=[close,input,...menuLinks.filter(item=>!item.a.hidden).map(item=>item.a)];
        const first=nodes[0], last=nodes[nodes.length-1];
        if(event.shiftKey && doc.activeElement===first) {
          event.preventDefault();last.focus();
        } else if(!event.shiftKey && doc.activeElement===last){
          event.preventDefault();first.focus();
        }
      }
    });
    root.addEventListener("hashchange",()=>{
      setActive();
      toggle(false,false);
      if (typeof root.scrollTo==="function") root.scrollTo(0,0);
    });
    if (typeof root.matchMedia==="function") {
      const query = root.matchMedia("(min-width: 761px)");
      if (query.addEventListener) query.addEventListener("change",e=>{
        if(e.matches) toggle(false,false);
      });
    }
    setActive();
  }
  root.RakursMobileNavigation=Object.freeze({init,routeOf,groups});
  if (root.document.readyState==="loading") {
    root.document.addEventListener("DOMContentLoaded",init,{once:true});
  } else init();
})(window);
