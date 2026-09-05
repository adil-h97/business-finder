import { api } from "./api.js";

export const state = {
  config: null,
  leads: [],
  weights: null,
  view: "leads",
  selected: null,
  filters: { text: "", search: "", status: "", issues: new Set(), tiers: new Set() },
  sort: { key: "score", dir: -1 },
  showAllFacets: false,
};

const listeners = new Set();
export const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const emit = () => listeners.forEach((fn) => fn());

/* ---------- scoring (mirrors scoring.py using server constants) ---------- */

export function defaultWeights() {
  return { ...state.config.weights, no_website: state.config.issue_scores.NO_WEBSITE };
}

export function scoreOf(l) {
  const w = state.weights;
  const web = l.issue === "NO_WEBSITE"
    ? w.no_website
    : (state.config.issue_scores[l.issue] ?? 10);
  const n = l.review_count || 0;
  const reviews = Math.min(w.reviews,
    w.reviews * Math.log10(1 + n) / Math.log10(1 + w.review_saturation));
  const rating = Math.max(0, Math.min(w.rating, ((l.rating || 0) - 4) * w.rating));
  const phone = l.phone ? w.phone : 0;
  const ticket = l.is_high_ticket ? w.high_ticket : 0;
  const business = reviews + rating + phone + ticket;
  return {
    total: Math.round((web + business) * 10) / 10,
    web, business: Math.round(business * 10) / 10,
    parts: { reviews, rating, phone, ticket },
  };
}

export function tierOf(total) {
  for (const [cut, label] of state.config.tiers) if (total >= cut) return label;
  return "D";
}

export function rescore() {
  for (const l of state.leads) {
    l._s = scoreOf(l);
    l._tier = tierOf(l._s.total);
  }
}

/* ---------- what each website problem means, and how to open ----------
   The score says who to call. This says what to say — without it the app
   stops at the hard part.                                                */

export const ISSUE_INFO = {
  SOCIAL_ONLY: {
    title: "Social profile listed as their website",
    why: "Their Google listing sends people to a page they don't own and can't rank in search. No way to book, quote, or capture a lead — and if the platform changes, they lose it.",
    open: (l) => `I found ${l.name} on Google — ${l.review_count} reviews, that's a serious reputation. But the website link goes to your social page. Everyone who clicks lands somewhere you don't control.`,
  },
  DEAD_GOOGLE_BUILDER: {
    title: "Dead Google-built site",
    why: "Google shut down its Business Profile website builder in March 2024. These links now go nowhere. The owner usually has no idea.",
    open: (l) => `Your Google listing links to a site Google switched off last year — anyone clicking "Website" hits a dead end. With ${l.review_count} reviews you're sending real interest into a wall.`,
  },
  DEAD_DOMAIN: {
    title: "Domain doesn't exist",
    why: "The domain has no DNS record at all — usually a lapsed registration. Every click on their listing fails.",
    open: (l) => `I noticed the website on your Google listing isn't loading — the domain looks like it expired. You've got ${l.review_count} reviews driving people there and they're hitting an error.`,
  },
  PARKED: {
    title: "Domain parked",
    why: "The domain shows a registrar placeholder or 'coming soon'. Looks abandoned to anyone who clicks.",
    open: (l) => `The website on your Google listing is showing a holding page rather than your business. Worth fixing given ${l.review_count} people have reviewed you.`,
  },
  UNREACHABLE: {
    title: "Website returns an error",
    why: "The page 404s or errors. They're paying for a listing that points at a broken link.",
    open: (l) => `The link on your Google listing returns an error page. With ${l.rating}★ from ${l.review_count} reviews, that's a lot of interest going nowhere.`,
  },
  CERT_ERROR: {
    title: "Broken SSL certificate",
    why: "Browsers show a full-page red warning before anyone reaches the site. Most visitors turn back — this is worse than having no site.",
    open: (l) => `Anyone visiting your website right now gets a full-screen security warning from their browser before they even see the page. It's a lapsed certificate — cheap to fix, but it's actively costing you customers.`,
  },
  SERVER_ERROR: {
    title: "Server erroring",
    why: "The site returns a 5xx. Down, or badly broken.",
    open: (l) => `Your website is returning a server error at the moment — it looks down rather than slow.`,
  },
  NO_HTTPS: {
    title: "No HTTPS",
    why: "Chrome marks the site 'Not secure' in the address bar, and Google has used HTTPS as a ranking signal for years.",
    open: (l) => `Your website is still on plain HTTP, so Chrome shows visitors a "Not secure" warning next to your address. For a business with ${l.review_count} reviews that's an unnecessary first impression.`,
  },
  NOT_MOBILE: {
    title: "Not built for phones",
    why: "No viewport tag, so the site renders desktop-width on a phone — pinch-and-zoom. The large majority of local searches are mobile.",
    open: (l) => `I pulled up your website on my phone and it loads the desktop version — you have to pinch to zoom to read anything. Most people finding you on Google Maps are on a phone.`,
  },
  FREE_BUILDER: {
    title: "Free builder subdomain",
    why: "On a free plan with the builder's branding in the address. Reads as temporary, and ranks poorly.",
    open: (l) => `Your website is on a free-plan address rather than your own domain. With ${l.review_count} reviews behind you it undersells the business.`,
  },
  NO_WEBSITE: {
    title: "No website at all",
    why: "Invisible outside Google Maps. Often deliberate — plenty are busy enough without one, so lead with a reason, not an offer.",
    open: (l) => `You've got ${l.rating}★ from ${l.review_count} reviews and no website — which tells me word of mouth is working. Are you turning work away, or would you take more?`,
  },
  THIN: {
    title: "Almost no content",
    why: "Barely any HTML. Likely a placeholder or a broken build.",
    open: (l) => `Your website loads but there's almost nothing on it — looks like a placeholder that never got finished.`,
  },
  STALE: {
    title: "Looks abandoned",
    why: "Copyright notice is years old. Signals to visitors that the business may not be active.",
    open: (l) => `Your website still shows a copyright from a few years back — small thing, but it makes people wonder whether you're still trading.`,
  },
  SLOW: {
    title: "Slow to load",
    why: "Over three seconds. Most mobile visitors abandon before it renders.",
    open: (l) => `Your website takes a few seconds to load on mobile, and that's where most people abandon.`,
  },
  TIMEOUT: {
    title: "Timed out — verify first",
    why: "Two timeouts, but the domain resolves. Often a slow host or one blocking automated requests. Low confidence.",
    open: (l) => `Check the site yourself before calling — our scan timed out, which sometimes just means the host is slow.`,
  },
  BLOCKED: {
    title: "Couldn't check — probably fine",
    why: "The server refused our request (403/429). Tells us nothing about the site. Assume it's fine unless you see otherwise.",
    open: (l) => `We couldn't inspect this site — it blocked the scan. Look before you call; there may be no problem at all.`,
  },
  OK: {
    title: "No obvious problem",
    why: "Site is reachable, secure, mobile-ready and current. Not a website lead — though they may still want SEO or booking work.",
    open: (l) => `Their site is fine. If you approach them, lead with rankings or bookings rather than a rebuild.`,
  },
};

export const HOT_ISSUES = new Set([
  "SOCIAL_ONLY", "DEAD_GOOGLE_BUILDER", "DEAD_DOMAIN", "PARKED",
  "UNREACHABLE", "CERT_ERROR", "SERVER_ERROR",
]);
export const WEAK_ISSUES = new Set(["OK", "BLOCKED", "TIMEOUT"]);

export const issueInfo = (k) => ISSUE_INFO[k] || {
  title: k, why: "", open: () => "",
};

/* ---------- filtering ---------- */

export function scoped() {
  const t = state.filters.text.toLowerCase();
  return state.leads.filter((l) => {
    const f = state.filters;
    if (f.search && !(l.searches || []).includes(f.search)) return false;
    if (f.status && l.status !== f.status) return false;
    if (t && !`${l.name} ${l.category} ${l.address} ${l.notes}`.toLowerCase().includes(t))
      return false;
    return true;
  });
}

export function visible() {
  const f = state.filters;
  const rows = scoped().filter((l) => {
    if (f.issues.size && !f.issues.has(l.issue)) return false;
    if (f.tiers.size && !f.tiers.has(l._tier)) return false;
    return true;
  });
  const { key, dir } = state.sort;
  return rows.sort((a, b) => {
    const g = (l) => (key === "score" ? l._s.total : l[key]);
    const x = g(a), y = g(b);
    if (x === y) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
  });
}

export const hasFilters = () => {
  const f = state.filters;
  return !!(f.text || f.search || f.status || f.issues.size || f.tiers.size);
};

export function clearFilters() {
  state.filters = { text: "", search: "", status: "", issues: new Set(), tiers: new Set() };
}

/* ---------- loading ---------- */

export async function loadConfig() {
  state.config = await api.config();
  if (!state.weights) state.weights = defaultWeights();
}

export async function loadLeads() {
  state.leads = await api.leads();
  rescore();
}
