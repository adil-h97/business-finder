const json = async (res) => {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
};
const get = (u) => fetch(u).then(json);
const post = (u, body) => fetch(u, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
}).then(json);

export const api = {
  config:   () => get("/api/config"),
  leads:    () => get("/api/leads"),
  compare:  () => get("/api/compare"),
  status:   () => get("/api/search/status"),
  run:      (b) => post("/api/search", b),
  geocode:  (b) => post("/api/geocode", b),
  saveKey:  (key) => post("/api/key", { key }),
  testKey:  () => post("/api/key/test"),
  outreach: (id, b) => post(`/api/lead/${encodeURIComponent(id)}/outreach`, b),
  delSearch:(query, city) => post("/api/searches/delete", { query, city }),
};
