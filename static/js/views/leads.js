import { $, esc, plural, STATUS_CLASS } from "../ui.js";
import { state, visible, scoped, hasFilters, clearFilters, emit,
         HOT_ISSUES, WEAK_ISSUES, issueInfo } from "../store.js";
import { openLead } from "../drawer.js";

const FACET_LIMIT = 7;

export function render() {
  const rows = visible();
  window.__visibleRows = rows;
  renderStats();
  renderControls();
  renderFacets();

  $("#shown").textContent = rows.length === state.leads.length
    ? plural(rows.length, "lead")
    : `${rows.length} of ${state.leads.length}`;
  $("#btnClearF").hidden = !hasFilters();

  const body = $("#leadsBody");

  if (!state.leads.length) {
    body.innerHTML = `<div class="tbl-wrap"><div class="empty">
      <div class="ic">🔍</div><h3>No leads yet</h3>
      <p>Run your first sweep to find businesses with strong reviews and weak websites.</p>
      <button class="btn primary" data-go="discover">Go to Discover</button></div></div>`;
    return;
  }
  if (!rows.length) {
    body.innerHTML = `<div class="tbl-wrap"><div class="empty">
      <div class="ic">∅</div><h3>Nothing matches</h3>
      <p>No leads fit the filters you've set.</p>
      <button class="btn" id="emptyClear">Clear filters</button></div></div>`;
    $("#emptyClear").onclick = () => { clearFilters(); emit(); };
    return;
  }

  const cols = [
    ["score", "Score"], ["name", "Business"], ["issue", "Website problem"],
    ["review_count", "Reviews"], ["rating", "Rating"], ["status", "Status"],
  ];
  body.innerHTML = `<div class="tbl-wrap"><table class="leads"><thead><tr>${
    cols.map(([k, label]) =>
      `<th data-sort="${k}">${label}${state.sort.key === k
        ? `<span class="dir"> ${state.sort.dir > 0 ? "↑" : "↓"}</span>` : ""}</th>`).join("")
  }</tr></thead><tbody>${rows.map(row).join("")}</tbody></table></div>`;

  body.querySelectorAll("th[data-sort]").forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.sort;
      state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : -1 };
      emit();
    };
  });
  body.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.onclick = () => openLead(tr.dataset.id);
  });
}

function row(l) {
  const info = issueInfo(l.issue);
  const cls = HOT_ISSUES.has(l.issue) ? "hot" : WEAK_ISSUES.has(l.issue) ? "weak" : "";
  return `<tr data-id="${esc(l.place_id)}" class="${l.status === "dead" ? "done" : ""} ${
    l.place_id === state.selected ? "sel" : ""}">
    <td><div class="score-cell"><span class="tier ${l._tier}">${l._tier}</span>
      <span class="n">${l._s.total}</span></div></td>
    <td class="biz"><div class="nm">${esc(l.name)}</div>
      <div class="mt">${esc(l.category || "")}${l.city ? " · " + esc(l.city) : ""}</div></td>
    <td><span class="tag ${cls}">${esc(info.title)}</span></td>
    <td class="num">${l.review_count ?? ""}</td>
    <td class="num">${l.rating ?? ""}</td>
    <td><span class="dot ${STATUS_CLASS[l.status] || "new"}"></span>${esc(l.status)}</td>
  </tr>`;
}

function renderStats() {
  const all = state.leads;
  const a = all.filter((l) => l._tier === "A").length;
  const hot = all.filter((l) => HOT_ISSUES.has(l.issue)).length;
  const untouched = all.filter((l) => l.status === "new").length;
  const live = all.filter((l) => l.status === "interested").length;
  $("#leadStats").innerHTML = `
    ${stat("Total leads", all.length, `across ${plural(state.config.searches.length, "search", "searches")}`)}
    ${stat("Tier A", a, "worth calling first", true)}
    ${stat("Easy openers", hot, "broken or fake sites")}
    ${stat("Not yet contacted", untouched, live ? `${live} interested` : "")}`;
}

const stat = (k, v, foot, accent) => `<div class="stat">
  <div class="k">${k}</div><div class="v${accent ? " accent" : ""}">${v}</div>
  <div class="foot">${foot || "&nbsp;"}</div></div>`;

function renderControls() {
  const f = state.filters;
  if ($("#q").value !== f.text) $("#q").value = f.text;

  $("#fSearch").innerHTML = `<option value="">All searches</option>` +
    state.config.searches.map((s) =>
      `<option value="${esc(s.key)}"${s.key === f.search ? " selected" : ""}>${
        esc(s.label)} (${s.count})</option>`).join("");

  $("#fStatus").innerHTML = `<option value="">Any status</option>` +
    state.config.statuses.map((s) =>
      `<option${s === f.status ? " selected" : ""}>${esc(s)}</option>`).join("");

  $("#fSort").value = state.sort.key;
}

function renderFacets() {
  const base = scoped();
  const tiers = {}, issues = {};
  for (const l of base) {
    tiers[l._tier] = (tiers[l._tier] || 0) + 1;
    issues[l.issue] = (issues[l.issue] || 0) + 1;
  }
  const f = state.filters;
  const pill = (key, label, n, on) =>
    `<span class="pill${on ? " on" : ""}" data-k="${esc(key)}">${esc(label)}<span class="n">${n}</span></span>`;

  let out = ["A", "B", "C", "D"].filter((t) => tiers[t])
    .map((t) => pill(`tier:${t}`, `Tier ${t}`, tiers[t], f.tiers.has(t))).join("");

  const sorted = Object.entries(issues).sort((a, b) => {
    // Surface the sellable problems first; "no obvious problem" last.
    const rank = (k) => (HOT_ISSUES.has(k) ? 0 : WEAK_ISSUES.has(k) ? 2 : 1);
    return rank(a[0]) - rank(b[0]) || b[1] - a[1];
  });
  const shown = state.showAllFacets ? sorted : sorted.slice(0, FACET_LIMIT);
  out += `<span style="width:10px"></span>` + shown
    .map(([k, n]) => pill(`issue:${k}`, issueInfo(k).title, n, f.issues.has(k))).join("");
  if (sorted.length > FACET_LIMIT) {
    out += `<span class="pill more" data-k="toggle">${
      state.showAllFacets ? "Show fewer" : `+${sorted.length - FACET_LIMIT} more`}</span>`;
  }

  const el = $("#facets");
  el.innerHTML = out;
  el.querySelectorAll(".pill").forEach((p) => {
    p.onclick = () => {
      const k = p.dataset.k;
      if (k === "toggle") { state.showAllFacets = !state.showAllFacets; return emit(); }
      const [kind, val] = k.split(":");
      const set = kind === "tier" ? f.tiers : f.issues;
      set.has(val) ? set.delete(val) : set.add(val);
      emit();
    };
  });
}

export function bind() {
  $("#q").oninput = (e) => { state.filters.text = e.target.value; emit(); };
  $("#fSearch").onchange = (e) => { state.filters.search = e.target.value; emit(); };
  $("#fStatus").onchange = (e) => { state.filters.status = e.target.value; emit(); };
  $("#fSort").onchange = (e) => { state.sort = { key: e.target.value, dir: -1 }; emit(); };
  $("#btnClearF").onclick = () => { clearFilters(); emit(); };
}
