import { $, esc, toast, debounce, relTime, STATUS_CLASS } from "./ui.js";
import { state, emit, issueInfo, HOT_ISSUES } from "./store.js";
import { api } from "./api.js";

const saveNotes = debounce(async (id, text) => {
  await api.outreach(id, { notes: text });
  const l = state.leads.find((x) => x.place_id === id);
  if (l) l.notes = text;
  const flag = $("#notesSaved");
  if (flag) { flag.classList.add("show"); setTimeout(() => flag.classList.remove("show"), 1400); }
  emit();
}, 700);

export function openLead(id) {
  state.selected = id;
  render();
}

export function closeLead() {
  state.selected = null;
  $("#drawer").classList.remove("show");
  $("#scrim").classList.remove("show");
  setTimeout(() => {
    if (!state.selected) { $("#drawer").hidden = true; $("#scrim").hidden = true; }
  }, 200);
}

export function render() {
  const l = state.leads.find((x) => x.place_id === state.selected);
  const d = $("#drawer");
  if (!l) { closeLead(); return; }

  const info = issueInfo(l.issue);
  const s = l._s;
  const bar = (label, val, max) => `
    <div class="b"><span class="muted">${label}</span>
      <div class="track"><i style="width:${Math.round(100 * val / max)}%"></i></div>
      <span class="val">${Math.round(val)}</span></div>`;
  const w = state.weights;

  d.innerHTML = `
    <div class="dhd">
      <span class="tier ${l._tier}" style="margin-top:2px">${l._tier}</span>
      <div style="flex:1;min-width:0">
        <h2>${esc(l.name)}</h2>
        <div class="mt">${esc(l.category || "")} · ${esc(l.address || "")}</div>
      </div>
      <button class="btn ghost sm" id="dClose" aria-label="Close">✕</button>
    </div>

    <div class="dbd">
      <div class="sect">
        <div class="diag ${HOT_ISSUES.has(l.issue) ? "hot" : ""}">
          <div class="t">${esc(info.title)}</div>
          <div class="d">${esc(info.why)}</div>
          ${l.audit_note ? `<div class="d mono" style="margin-top:7px;font-size:11.5px">${esc(l.audit_note)}</div>` : ""}
        </div>
      </div>

      ${info.open ? `<div class="sect">
        <h4>Opening line</h4>
        <div class="pitch">
          <div class="q" id="pitchText">${esc(info.open(l))}</div>
          <div class="act">
            <button class="btn sm" id="dCopy">Copy</button>
            ${l.phone ? `<a class="btn sm primary" href="tel:${esc(l.phone.replace(/\s/g, ""))}">Call ${esc(l.phone)}</a>` : ""}
          </div>
        </div>
      </div>` : ""}

      <div class="sect">
        <h4>The business</h4>
        <dl class="kv">
          <dt>Rating</dt><dd><b>${l.rating ?? "—"}★</b> from ${l.review_count} reviews</dd>
          <dt>Phone</dt><dd>${l.phone ? esc(l.phone) : '<span class="dim">none listed</span>'}</dd>
          <dt>Website</dt><dd>${l.website
            ? `<a href="${esc(l.website)}" target="_blank" rel="noopener noreferrer">${esc(l.website.slice(0, 52))}</a>`
            : '<span class="dim">none</span>'}</dd>
          ${l.platform ? `<dt>Built with</dt><dd>${esc(l.platform)}</dd>` : ""}
          <dt>Ticket size</dt><dd>${l.is_high_ticket
            ? '<span class="tag good">high-ticket category</span>'
            : '<span class="dim">not a high-ticket category</span>'}</dd>
          <dt>Maps</dt><dd><a href="${esc(l.maps_uri)}" target="_blank" rel="noopener noreferrer">open listing</a></dd>
        </dl>
      </div>

      <div class="sect">
        <h4>Why it scores ${s.total}</h4>
        <div class="bars">
          ${bar("Website problem", s.web, 50)}
          ${bar("Review volume", s.parts.reviews, w.reviews)}
          ${bar("Star rating", s.parts.rating, w.rating)}
          ${bar("High ticket", s.parts.ticket, w.high_ticket)}
          ${bar("Has phone", s.parts.phone, w.phone)}
        </div>
      </div>

      <div class="sect">
        <h4>Your notes <span class="saved" id="notesSaved">saved</span></h4>
        <textarea class="notes-area" id="dNotes"
          placeholder="Who you spoke to, what they said, when to follow up…">${esc(l.notes || "")}</textarea>
      </div>
    </div>

    <div class="dft">
      <select id="dStatus" style="width:150px">
        ${state.config.statuses.map((st) =>
          `<option${st === l.status ? " selected" : ""}>${esc(st)}</option>`).join("")}
      </select>
      <span class="dim" style="font-size:11.5px">
        ${l.last_contacted ? `first contacted ${relTime(l.last_contacted)}` : "not contacted yet"}
      </span>
      <span style="flex:1"></span>
      <button class="btn sm ghost" id="dPrev" title="Previous (k)">↑</button>
      <button class="btn sm ghost" id="dNext" title="Next (j)">↓</button>
    </div>`;

  d.hidden = false;
  $("#scrim").hidden = false;
  requestAnimationFrame(() => { d.classList.add("show"); $("#scrim").classList.add("show"); });

  $("#dClose").onclick = closeLead;
  $("#dNotes").oninput = (e) => saveNotes(l.place_id, e.target.value);
  $("#dStatus").onchange = async (e) => {
    const res = await api.outreach(l.place_id, { status: e.target.value });
    Object.assign(l, { status: res.status, last_contacted: res.last_contacted });
    toast(`${l.name} → ${res.status}`);
    emit(); render();
  };
  const copy = $("#dCopy");
  if (copy) copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText($("#pitchText").textContent.trim());
      toast("Opening line copied");
    } catch { toast("Couldn't copy — select the text instead", "err"); }
  };
  $("#dPrev").onclick = () => step(-1);
  $("#dNext").onclick = () => step(1);
}

export function step(dir) {
  const rows = window.__visibleRows || [];
  const i = rows.findIndex((l) => l.place_id === state.selected);
  const next = rows[i + dir];
  if (next) openLead(next.place_id);
}
