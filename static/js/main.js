import { $, $$, esc, toast } from "./ui.js";
import { state, subscribe, emit, loadConfig, loadLeads, rescore } from "./store.js";
import * as leads from "./views/leads.js";
import * as pipeline from "./views/pipeline.js";
import * as discover from "./views/discover.js";
import * as settings from "./views/settings.js";
import * as drawer from "./drawer.js";

const VIEWS = { leads, pipeline, discover, settings };

function renderChrome() {
  const c = state.config;
  $("#quota").innerHTML = ["text", "nearby"].map((sku) => {
    const d = c.spend.skus[sku];
    const cls = d.pct >= 100 ? "over" : d.pct >= 75 ? "warn" : "";
    return `<div class="q" title="${esc(d.label)} — counted by this app, not read from Google">
      <div class="lbl"><span>${esc(d.label)}</span><span class="tnum">${d.used}/${d.limit}</span></div>
      <div class="bar"><i class="${cls}" style="width:${Math.min(100, d.pct)}%"></i></div>
    </div>`;
  }).join("");

  $("#navLeads").textContent = state.leads.length || "";
  const engaged = state.leads.filter((l) => l.status !== "new").length;
  $("#navPipe").textContent = engaged || "";

  $$("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === state.view));
  Object.keys(VIEWS).forEach((v) => { $(`#view-${v}`).hidden = v !== state.view; });
}

function renderAll() {
  renderChrome();
  VIEWS[state.view].render();
  if (state.selected) drawer.render();
  $$("[data-go]").forEach((b) => {
    b.onclick = () => { location.hash = "#" + b.dataset.go; };
  });
}

function route() {
  const v = (location.hash || "#leads").slice(1);
  state.view = VIEWS[v] ? v : "leads";
  renderAll();
  $(".view:not([hidden])")?.scrollTo(0, 0);
}

async function refresh() {
  await Promise.all([loadConfig(), loadLeads()]);
  rescore();
  emit();
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await refresh();
  } catch (e) {
    document.body.innerHTML =
      `<div class="wrap narrow"><div class="card"><div class="bd">
        <h3>Couldn't reach the server</h3>
        <p class="muted">${esc(e.message)}</p></div></div></div>`;
    return;
  }

  Object.values(VIEWS).forEach((v) => v.bind && v.bind());
  subscribe(renderAll);
  window.addEventListener("hashchange", route);
  window.addEventListener("data-changed", refresh);

  $("#nav").onclick = (e) => {
    const b = e.target.closest("button[data-view]");
    if (b) location.hash = "#" + b.dataset.view;
  };
  $("#scrim").onclick = drawer.closeLead;

  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
    if (e.key === "Escape") {
      if (state.selected) return drawer.closeLead();
      if (typing) e.target.blur();
      return;
    }
    if (typing) return;
    if (e.key === "/") { e.preventDefault(); location.hash = "#leads"; $("#q").focus(); }
    if (state.selected && (e.key === "j" || e.key === "ArrowDown")) {
      e.preventDefault(); drawer.step(1);
    }
    if (state.selected && (e.key === "k" || e.key === "ArrowUp")) {
      e.preventDefault(); drawer.step(-1);
    }
  });

  route();
});
