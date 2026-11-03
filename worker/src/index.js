// Kith's version check and site count, a Cloudflare Worker with a D1 database.
//   check.kithgenealogy.org/v1/check  Each Kith site asks once a day (app/updates.py), sending its random ID and
//                                     version. The answer is the newest release. One row per site is kept.
//   stats.kithgenealogy.org           How many sites run Kith. Behind Cloudflare Access; checked here as well.

const SITE = /^[0-9a-f]{32}$/;
const VERSION = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;
const LIVE_DAYS = 30; // a site counts as running if it asked within this many days

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.hostname === env.STATS_HOST) {
      return (await allowed(req, env)) ? stats(env) : new Response("Forbidden", { status: 403 });
    }
    if (url.pathname === "/v1/check" && req.method === "POST") return check(req, env);
    return new Response("Not found", { status: 404 });
  },
};

export async function check(req, env) {
  const text = await req.text();
  let body;
  try {
    body = text.length <= 1024 ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!body || typeof body.site !== "string" || typeof body.version !== "string" ||
      !SITE.test(body.site) || !VERSION.test(body.version)) {
    return new Response("Bad request", { status: 400 });
  }
  await env.DB.prepare(
    "INSERT INTO sites (site, version, first_seen, last_seen) VALUES (?1, ?2, date('now'), date('now'))" +
    " ON CONFLICT(site) DO UPDATE SET version = ?2, last_seen = date('now')",
  ).bind(body.site, body.version).run();
  return Response.json({ latest: await latest() });
}

// The newest GitHub release, cached for an hour so GitHub is asked a few times an hour at most.
async function latest() {
  try {
    const r = await fetch("https://api.github.com/repos/dreed47/kith/releases/latest", {
      headers: { "User-Agent": "kith-check", Accept: "application/vnd.github+json" },
      cf: { cacheTtl: 3600, cacheEverything: true },
    });
    const tag = r.ok ? String((await r.json()).tag_name || "").replace(/^v/, "") : "";
    return VERSION.test(tag) ? tag : null;
  } catch {
    return null;
  }
}

// Access puts a signed token on every request it lets through. Checking it here too means the page stays closed
// if the Access app is ever removed or misconfigured.
export async function allowed(req, env) {
  const token = req.headers.get("Cf-Access-Jwt-Assertion");
  if (!token || !env.ACCESS_TEAM || !env.ACCESS_AUD) return false;
  try {
    const [h, p, s] = token.split(".");
    const header = JSON.parse(b64text(h));
    const claims = JSON.parse(b64text(p));
    const issuer = `https://${env.ACCESS_TEAM}.cloudflareaccess.com`;
    const certs = await (await fetch(`${issuer}/cdn-cgi/access/certs`)).json();
    const jwk = certs.keys.find((k) => k.kid === header.kid);
    if (!jwk || header.alg !== "RS256") return false;
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false,
      ["verify"]);
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64bytes(s),
      new TextEncoder().encode(`${h}.${p}`));
    const aud = [].concat(claims.aud);
    return ok && claims.iss === issuer && aud.includes(env.ACCESS_AUD) && claims.exp * 1000 > Date.now();
  } catch {
    return false;
  }
}

function b64bytes(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function b64text(s) {
  return new TextDecoder().decode(b64bytes(s));
}

export async function stats(env) {
  const one = async (sql) => (await env.DB.prepare(sql).first()).n;
  const live = `last_seen >= date('now', '-${LIVE_DAYS} days')`;
  const n = {
    live: await one(`SELECT COUNT(*) AS n FROM sites WHERE ${live}`),
    week: await one("SELECT COUNT(*) AS n FROM sites WHERE last_seen >= date('now', '-7 days')"),
    month: await one("SELECT COUNT(*) AS n FROM sites WHERE first_seen >= date('now', 'start of month')"),
    ever: await one("SELECT COUNT(*) AS n FROM sites"),
  };
  const versions = (await env.DB.prepare(
    `SELECT version, COUNT(*) AS n FROM sites WHERE ${live} GROUP BY version ORDER BY n DESC, version DESC`,
  ).all()).results;
  const months = (await env.DB.prepare(
    "SELECT substr(first_seen, 1, 7) AS month, COUNT(*) AS n FROM sites GROUP BY month ORDER BY month DESC LIMIT 12",
  ).all()).results;
  // Every value shown is a count, a version that matched VERSION, or a yyyy-mm month: nothing needs escaping.
  const rows = (list, key) => list.map((r) => `<tr><td>${r[key]}</td><td>${r.n}</td></tr>`).join("") ||
    '<tr><td colspan="2">None yet</td></tr>';
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Kith sites</title>
<style>
:root { --bg: #f4ecdc; --card: #fffaf0; --text: #2e2318; --muted: #6f5c45; --accent: #a8431a; --line: #e0d2b6; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #17110b; --card: #221a12; --text: #ecdfc5; --muted: #b29d7f; --accent: #e3a66a; --line: #3a2d20; } }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 32px 16px; }
h1 { font-family: Georgia, serif; font-weight: 600; margin: 0 0 4px; }
p { color: var(--muted); margin: 0 0 24px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 24px; }
.tile, table { background: var(--card); border: 1px solid var(--line); border-radius: 12px; }
.tile { padding: 16px; }
.tile b { display: block; font-size: 2.2rem; color: var(--accent); line-height: 1.1; }
.tile span { color: var(--muted); font-size: .9rem; }
h2 { font-size: 1.1rem; margin: 24px 0 8px; }
table { width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden; }
td { padding: 8px 14px; border-top: 1px solid var(--line); }
tr:first-child td { border-top: 0; }
td:last-child { text-align: right; font-variant-numeric: tabular-nums; }
</style></head><body><main>
<h1>Kith sites</h1>
<p>From the daily version check. A site counts as running if it checked in within ${LIVE_DAYS} days.
Sites that turned the check off aren't counted.</p>
<div class="tiles">
<div class="tile"><b>${n.live}</b><span>running (${LIVE_DAYS} days)</span></div>
<div class="tile"><b>${n.week}</b><span>seen this week</span></div>
<div class="tile"><b>${n.month}</b><span>new this month</span></div>
<div class="tile"><b>${n.ever}</b><span>ever seen</span></div>
</div>
<h2>Versions running</h2><table>${rows(versions, "version")}</table>
<h2>New sites by month</h2><table>${rows(months, "month")}</table>
</main></body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}
