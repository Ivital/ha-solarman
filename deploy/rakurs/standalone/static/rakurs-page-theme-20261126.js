"use strict";
(function () {
  const key="rakurs_theme";
  let theme="light";
  try { if(localStorage.getItem(key)==="dark")theme="dark"; }catch(_error){}
  function apply(){
    document.body.setAttribute("data-theme",theme);
    const button=document.getElementById("page-theme-toggle");
    if(button){
      button.textContent=theme==="light"?"☾ Темна тема":"☀ Світла тема";
      button.setAttribute("aria-pressed",theme==="dark"?"true":"false");
    }
  }
  function ready(){
    apply();
    const button=document.getElementById("page-theme-toggle");
    if(button)button.addEventListener("click",function(){
      theme=theme==="light"?"dark":"light";
      try {localStorage.setItem(key,theme);}catch(_error){}
      apply();
    });
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",ready);
  else ready();
})();
