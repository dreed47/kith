// Tests for the Worker, with Node's own SQLite standing in for D1 and fetch mocked: `node --test worker/test.mjs`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import worker from "./src/index.js";

function d1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("schema.sql", import.meta.url), "utf8"));
  const statement = (sql, args = []) => ({
    bind: (...a) => statement(sql, a),
    run: async () => db.prepare(sql).run(...args),
    first: async () => db.prepare(sql).get(...args),
    all: async () => ({ results: db.prepare(sql).all(...args) }),
  });
  return { db, prepare: (sql) => statement(sql) };
}

const SITE = "0123456789abcdef0123456789abcdef";
const enc = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");

async function setup(release = "v0.3.0") {
  const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const jwk = { ...(await crypto.subtle.exportKey("jwk", pair.publicKey)), kid: "k1" };
  globalThis.fetch = async (url) => {
    if (String(url).includes("api.github.com")) {
      return release === null ? new Response("nope", { status: 500 }) : Response.json({ tag_name: release });
    }
    return Response.json({ keys: [jwk] });
  };
  const env = { DB: d1(), STATS_HOST: "stats.kithgenealogy.org", ACCESS_TEAM: "team", ACCESS_AUD: "aud1" };
  const token = async (claims, kid = "k1") => {
    const h = enc({ alg: "RS256", kid }), p = enc(claims);
    const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, new TextEncoder().encode(`${h}.${p}`));
    return `${h}.${p}.${Buffer.from(sig).toString("base64url")}`;
  };
  return { env, token };
}

const ask = (env, body, path = "/v1/check") => worker.fetch(new Request(`https://check.kithgenealogy.org${path}`,
  { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body) }), env);

test("a check is counted once per site and answered with the newest release", async () => {
  const { env } = await setup();
  const r = await ask(env, { site: SITE, version: "0.2.0" });
  assert.deepEqual(await r.json(), { latest: "0.3.0" });
  await ask(env, { site: SITE, version: "0.3.0" });
  const rows = env.DB.db.prepare("SELECT * FROM sites").all();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].version, "0.3.0");
});

test("anything else is refused and nothing is stored", async () => {
  const { env } = await setup();
  for (const bad of ["not json", "null", { site: "short", version: "0.2.0" }, { site: SITE, version: "<b>" },
    { site: SITE }, { site: SITE.toUpperCase(), version: "0.2.0" }, { site: SITE, version: 2 },
    JSON.stringify({ site: SITE, version: "0.2.0", pad: "x".repeat(2000) })]) {
    assert.equal((await ask(env, bad)).status, 400);
  }
  assert.equal((await ask(env, { site: SITE, version: "0.2.0" }, "/other")).status, 404);
  assert.equal(env.DB.db.prepare("SELECT COUNT(*) AS n FROM sites").get().n, 0);
});

test("no release found: latest is null", async () => {
  for (const release of [null, "nightly"]) {
    const { env } = await setup(release);
    assert.deepEqual(await (await ask(env, { site: SITE, version: "0.2.0" })).json(), { latest: null });
  }
});

test("the stats page needs a valid Access token", async () => {
  const { env, token } = await setup();
  await ask(env, { site: SITE, version: "0.2.0" });
  const page = (t) => worker.fetch(new Request("https://stats.kithgenealogy.org/",
    { headers: t ? { "Cf-Access-Jwt-Assertion": t } : {} }), env);
  const good = { iss: "https://team.cloudflareaccess.com", aud: ["aud1"], exp: Date.now() / 1000 + 600 };
  const r = await page(await token(good));
  assert.equal(r.status, 200);
  const html = await r.text();
  assert.match(html, /<b>1<\/b><span>running/);
  assert.match(html, /<td>0\.2\.0<\/td><td>1<\/td>/);
  for (const bad of [null, "junk", await token({ ...good, aud: ["other"] }), await token({ ...good, exp: 1 }),
    await token({ ...good, iss: "https://evil.cloudflareaccess.com" }), await token(good, "unknown-key"),
    (await token(good)).slice(0, -4) + "AAAA"]) {
    assert.equal((await page(bad)).status, 403);
  }
  assert.equal((await worker.fetch(new Request("https://stats.kithgenealogy.org/",
    { headers: { "Cf-Access-Jwt-Assertion": await token(good) } }), { ...env, ACCESS_AUD: "" })).status, 403);
});
