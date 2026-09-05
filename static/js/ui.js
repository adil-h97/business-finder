export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = "toast" + (kind ? ` ${kind}` : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "err" ? 6000 : 3200);
}

export function debounce(fn, ms = 500) {
  const timers = new Map();
  return (key, ...args) => {
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(() => fn(key, ...args), ms));
  };
}

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

export function relTime(iso) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export const STATUS_CLASS = {
  "new": "new", "called": "called", "no answer": "noanswer",
  "interested": "interested", "dead": "dead",
};
