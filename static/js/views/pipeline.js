import { $, esc, relTime, plural, STATUS_CLASS } from "../ui.js";
import { state, emit } from "../store.js";
import { openLead } from "../drawer.js";

// 'new' is deliberately excluded — the pipeline is people you've engaged.
// Untouched leads live in the Leads view.
const COLUMNS = ["called", "no answer", "interested", "dead"];

export function render() {
  const engaged = state.leads.filter((l) => l.status !== "new");
  const body = $("#pipeBody");

  if (!engaged.length) {
    body.innerHTML = `<div class="card"><div class="empty">
      <div class="ic">📋</div><h3>Nothing in the pipeline yet</h3>
      <p>Set a status on a lead and it appears here. Open any lead from the
         Leads tab and mark it called, interested, or dead.</p>
      <button class="btn primary" data-go="leads">Browse leads</button></div></div>`;
    return;
  }

  const interested = engaged.filter((l) => l.status === "interested").length;
  const worked = engaged.length;
  const total = state.leads.length;

  body.innerHTML = `
    <div class="stats">
      ${stat("Worked", worked, `${Math.round(100 * worked / total)}% of ${total} leads`)}
      ${stat("Interested", interested, interested ? "follow these up" : "none yet", true)}
      ${stat("Awaiting reply", engaged.filter((l) => l.status === "called"
        || l.status === "no answer").length, "called, no outcome yet")}
      ${stat("Ruled out", engaged.filter((l) => l.status === "dead").length, "")}
    </div>
    <div class="board">${COLUMNS.map((st) => column(st, engaged)).join("")}</div>`;

  body.querySelectorAll(".mini[data-id]").forEach((c) => {
    c.onclick = () => openLead(c.dataset.id);
  });
  body.querySelectorAll("[data-go]").forEach((b) => {
    b.onclick = () => location.hash = "#" + b.dataset.go;
  });
}

const stat = (k, v, foot, accent) => `<div class="stat">
  <div class="k">${k}</div><div class="v${accent ? " accent" : ""}">${v}</div>
  <div class="foot">${foot || "&nbsp;"}</div></div>`;

function column(status, engaged) {
  const items = engaged
    .filter((l) => l.status === status)
    .sort((a, b) => b._s.total - a._s.total);
  return `<div class="col">
    <div class="ch"><span class="dot ${STATUS_CLASS[status]}"></span>${esc(status)}
      <span class="n">${items.length}</span></div>
    <div class="cb">${items.length
      ? items.map(card).join("")
      : `<div class="col-empty">nothing here</div>`}</div>
  </div>`;
}

function card(l) {
  return `<div class="mini" data-id="${esc(l.place_id)}">
    <div class="nm">${esc(l.name)}</div>
    <div class="mt">
      <span class="tier ${l._tier}" style="width:15px;height:15px;font-size:9px">${l._tier}</span>
      <span>${l._s.total}</span>
      <span>·</span>
      <span>${l.review_count}★${l.rating ? " " + l.rating : ""}</span>
      ${l.last_contacted ? `<span>· ${relTime(l.last_contacted)}</span>` : ""}
    </div>
    ${l.notes ? `<div class="nt">${esc(l.notes)}</div>` : ""}
  </div>`;
}
