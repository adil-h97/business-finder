import { $, esc, toast } from "../ui.js";
import { api } from "../api.js";
import { state, emit, defaultWeights, rescore } from "../store.js";

export function render() {
  const c = state.config;

  $("#keySrc").textContent = { env: "set via .env", stored: "stored locally",
                               missing: "not set" }[c.key.source] || "";
  $("#keyMask").value = c.key.masked || "(none set)";

  $("#usageBody").innerHTML = Object.entries(c.spend.skus).map(([sku, d]) => `
    <div style="margin-bottom:13px">
      <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
        <span>${esc(d.label)}</span>
        <span class="tnum muted">${d.used} / ${d.limit}</span></div>
      <div class="progress"><i style="width:${Math.min(100, d.pct)}%;${
        d.pct >= 100 ? "background:var(--danger)" : d.pct >= 75 ? "background:var(--warn)" : ""
      }"></i></div>
    </div>`).join("") + `<div class="help">${esc(c.spend.month)}</div>`;

  const w = state.weights;
  const sliders = [
    ["reviews", "Review volume", 40, "How much review count matters. The single best proxy for revenue."],
    ["rating", "Star rating", 40, "Deliberately small — 4.9 from 12 reviews beats nothing."],
    ["high_ticket", "High-ticket category", 40, "Roofing, dental, legal. One job pays for the site many times."],
    ["phone", "Has a phone number", 20, "You can't cold-call without one."],
    ["no_website", "\"No website\" weight", 60, "Set below broken-site scores: no site is often a deliberate choice."],
  ];
  $("#weightsBody").innerHTML = sliders.map(([k, label, max, help]) => `
    <div class="field">
      <span class="lbl">${label} <em style="float:right" class="tnum" data-v="${k}">${w[k]}</em></span>
      <input type="range" min="0" max="${max}" step="1" value="${w[k]}" data-w="${k}"
             style="width:100%;accent-color:var(--brand)">
      <div class="help">${help}</div>
    </div>`).join("");

  $("#weightsBody").querySelectorAll("input[data-w]").forEach((inp) => {
    inp.oninput = (e) => {
      const k = e.target.dataset.w;
      state.weights[k] = parseFloat(e.target.value);
      $(`[data-v="${k}"]`).textContent = state.weights[k];
      rescore();
      emit();
    };
  });

  $("#searchesBody").innerHTML = c.searches.length
    ? `<table class="cmp"><tbody>${c.searches.map((s) => `<tr>
        <td>${esc(s.label)}</td>
        <td class="num muted">${s.count} leads</td>
        <td style="width:1%"><button class="btn sm danger" data-del="${esc(s.key)}">Remove</button></td>
      </tr>`).join("")}</tbody></table>`
    : `<div class="empty"><p>No searches yet.</p></div>`;

  $("#searchesBody").querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      const s = c.searches.find((x) => x.key === b.dataset.del);
      if (b.dataset.armed !== "1") {
        b.dataset.armed = "1"; b.textContent = "Confirm";
        setTimeout(() => { b.dataset.armed = "0"; b.textContent = "Remove"; }, 4000);
        return;
      }
      const d = await api.delSearch(s.query, s.city);
      toast(`Removed "${s.label}" — ${d.deleted} leads dropped, ${d.kept} kept. Notes preserved.`);
      window.dispatchEvent(new CustomEvent("data-changed"));
    };
  });
}

export function bind() {
  $("#btnKeySave").onclick = async () => {
    try {
      const d = await api.saveKey($("#keyIn").value.trim());
      $("#keyIn").value = ""; $("#keyMask").value = d.masked;
      $("#keyMsg").textContent = "Saved.";
      toast("API key saved");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { $("#keyMsg").textContent = e.message; toast(e.message, "err"); }
  };
  $("#btnKeyTest").onclick = async () => {
    $("#keyMsg").textContent = "Testing…";
    try {
      const d = await api.testKey();
      $("#keyMsg").textContent = d.message;
      toast("Key works");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { $("#keyMsg").textContent = e.message; toast(e.message, "err"); }
  };
  $("#btnResetW").onclick = () => {
    state.weights = defaultWeights(); rescore(); emit();
  };
}
