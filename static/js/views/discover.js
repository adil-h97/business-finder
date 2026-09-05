import { $, esc, toast } from "../ui.js";
import { api } from "../api.js";
import { state } from "../store.js";

// Starting points ordered by fit: high ticket, not captured by a platform,
// and often running on Instagram alone.
const PRESETS = {
  "Aesthetics": ["aesthetics clinic", "medical spa", "botox clinic", "laser hair removal"],
  "Home improvement": ["windows and doors", "driveway paving", "landscape gardener",
                       "kitchen fitter", "bathroom fitter"],
  "Trades": ["roofer", "plumber", "electrician", "heating engineer"],
  "Professional": ["immigration solicitor", "conveyancing solicitor", "accountant",
                   "private dentist"],
};

let polling = null;

export function render() {
  $("#cityList").innerHTML = state.config.cities
    .map((c) => `<option value="${esc(c)}">`).join("");
  updateBudgetHint();
  loadCompare();
}

function updateBudgetHint() {
  const left = state.config.spend.skus.text.remaining;
  const n = parseInt($("#dMax").value, 10) || 0;
  $("#budgetHint").innerHTML = n > left
    ? `<span style="color:var(--danger)">Only ${left} left this month</span>`
    : `${left} left this month`;
}

async function checkLocation() {
  const box = $("#geoOut");
  box.hidden = false; box.className = "geo"; box.textContent = "Checking…";
  try {
    const d = await api.geocode({
      city: $("#dCity").value,
      radius_km: parseFloat($("#dRadius").value) || null,
    });
    const area = d.width_km * d.height_km;
    let warn = "", cls = "geo";
    if (area > 400) {
      cls = "geo warn";
      warn = `<div style="margin-top:5px"><b>Large area (${Math.round(area)} km²)</b> —
        a full sweep could cost hundreds of requests. Set a radius to bound it.</div>`;
    } else if (area < 4) {
      cls = "geo warn";
      warn = `<div style="margin-top:5px"><b>Small area (${area.toFixed(1)} km²)</b> —
        you may only find a handful of businesses.</div>`;
    }
    box.className = cls;
    box.innerHTML = `
      <div>Resolved to <b>${esc(d.formatted)}</b></div>
      <div class="muted">Search box <b>${d.width_km} × ${d.height_km} km</b>
        ${d.radius_km ? `· fixed ${d.radius_km} km radius` : "· Google's own boundary"}</div>
      ${warn}
      <div style="margin-top:6px"><a href="${esc(d.maps_url)}" target="_blank"
        rel="noopener noreferrer">Open in Google Maps</a> to confirm it's the right place</div>
      ${d.alternatives.length ? `<div class="dim" style="margin-top:5px;font-size:11.5px">
        Google also matched: ${d.alternatives.map(esc).join(" · ")}</div>` : ""}`;
  } catch (e) {
    box.className = "geo bad"; box.textContent = e.message;
  }
}

async function run() {
  try {
    await api.run({
      query: $("#dQuery").value,
      city: $("#dCity").value,
      radius_km: parseFloat($("#dRadius").value) || null,
      min_rating: parseFloat($("#dRating").value),
      min_reviews: parseInt($("#dReviews").value, 10),
      max_requests: parseInt($("#dMax").value, 10),
      min_cell_km: 2.0,
    });
  } catch (e) { return toast(e.message, "err"); }

  $("#btnRun").disabled = true;
  $("#btnRun").textContent = "Sweeping…";
  $("#runCard").hidden = false;
  $("#runCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
  polling = setInterval(poll, 900);
  poll();
}

async function poll() {
  const s = await api.status();
  $("#runLog").textContent = s.log.join("\n");
  $("#runLog").scrollTop = $("#runLog").scrollHeight;
  $("#runBar").style.width = `${Math.min(100, 100 * s.spent / Math.max(1, s.budget))}%`;
  $("#runPhase").textContent = s.running
    ? `${s.phase} — ${s.spent}/${s.budget} requests, ${s.found} found`
    : s.phase;

  if (!s.running && s.done) {
    clearInterval(polling);
    $("#btnRun").disabled = false;
    $("#btnRun").textContent = "Run sweep";
    if (s.error) toast(s.error, "err");
    else toast(`Sweep finished — ${s.kept} leads added`);
    window.dispatchEvent(new CustomEvent("data-changed"));
    loadCompare();
  }
}

async function loadCompare() {
  const rows = await api.compare();
  $("#cmpCard").hidden = rows.length < 2;
  if (rows.length < 2) return;
  const bestA = Math.max(...rows.map((r) => r.tiers.A));
  $("#cmpBody").innerHTML = `<table class="cmp"><thead><tr>
      <th>Vertical</th><th>Leads</th><th>Tier A</th><th>Requests</th>
      <th>A per 100 reqs</th><th>Best lead</th></tr></thead><tbody>${
    rows.map((r) => `<tr>
      <td>${esc(r.label)}</td>
      <td class="num">${r.count}</td>
      <td class="num ${r.tiers.A === bestA && bestA > 0 ? "win" : ""}">${r.tiers.A}</td>
      <td class="num muted">${r.requests}</td>
      <td class="num">${r.a_per_100_reqs ?? "—"}</td>
      <td class="muted" style="font-size:12px">${r.best
        ? `${esc(r.best.name.slice(0, 28))} <span class="dim">(${r.best.score})</span>` : "—"}</td>
    </tr>`).join("")}</tbody></table>
    ${rows.some((r) => r.requests < 15) ? `<div class="help" style="padding:12px 14px">
      Rates from fewer than ~15 requests are noisy — treat them as directional
      until each vertical has had a proper budget.</div>` : ""}`;
}

export function bind() {
  $("#presets").innerHTML = Object.keys(PRESETS)
    .map((k) => `<span class="pill" data-p="${esc(k)}">${esc(k)}</span>`).join("");
  $("#presets").onclick = (e) => {
    const p = e.target.closest(".pill"); if (!p) return;
    $("#dQuery").value = PRESETS[p.dataset.p].join("\n");
    toast(`${p.dataset.p}: ${PRESETS[p.dataset.p].length} verticals loaded`);
  };
  $("#btnGeo").onclick = checkLocation;
  $("#dCity").onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); checkLocation(); }
  };
  $("#dRadius").onchange = () => { if (!$("#geoOut").hidden) checkLocation(); };
  $("#dMax").oninput = updateBudgetHint;
  $("#btnRun").onclick = run;
}
