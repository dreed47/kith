# Version check and site count

A Cloudflare Worker with a D1 database, on the Cloudflare account that holds kithgenealogy.org.

- `check.kithgenealogy.org/v1/check`: each Kith site asks once a day (`app/updates.py`), sending a random site ID
  and its version number. The Worker answers with the newest GitHub release and keeps one row per site: the ID,
  the version, and the first and last days it asked. No addresses, names or anything else about the site.
- `stats.kithgenealogy.org`: how many sites run Kith (checked in within 30 days), this week, new this month, ever,
  the versions running and new sites by month. Behind Cloudflare Access; the Worker checks the Access token too,
  so the page stays closed even if Access is ever switched off.

Tests: `node --test worker/test.mjs` (Node 24; CI runs them).

All of this fits Cloudflare's free plan: Workers allow 100,000 requests a day and D1 100,000 writes a day; each
Kith site makes one of each a day.

## Set it up (once, about 20 minutes)

You need Node (already on the workstation) and a terminal in this folder:

    cd ~/code/kith/worker

`npx wrangler@4` downloads Cloudflare's command-line tool the first time; later runs reuse it.

### 1. Log in to Cloudflare

    npx wrangler@4 login

A browser tab opens. Log in if asked, then click **Allow**. Back in the terminal it says "Successfully logged in".
Check it's the right account:

    npx wrangler@4 whoami

It lists your account name. If it lists more than one account, copy the ID of the one that holds
kithgenealogy.org and add `account_id = "that-id"` under `name = "kith-check"` in `wrangler.toml`.

### 2. Make the database

    npx wrangler@4 d1 create kith-sites

It prints a block that includes `database_id = "…"` (a long ID; `npx wrangler@4 d1 list` shows it again later). If it asks whether to add the database to your
config, answer **no**: `wrangler.toml` already has the entry. Instead, open `wrangler.toml` and replace
`REPLACE-with-the-id-from-wrangler-d1-create` with that ID. Then make the table:

    npx wrangler@4 d1 execute kith-sites --remote --file schema.sql

Check it worked (it should list `sites`):

    npx wrangler@4 d1 execute kith-sites --remote --command "SELECT name FROM sqlite_master WHERE type = 'table'"

### 3. Deploy the Worker

    npx wrangler@4 deploy

It ends with a list that includes `check.kithgenealogy.org (custom domain)` and `stats.kithgenealogy.org (custom
domain)`. Cloudflare makes the DNS records for both by itself; don't add them on the DNS page. (If you ever added
`check` or `stats` records by hand, delete them first, or the deploy fails.)

The stats page isn't open to anyone yet: until steps 4 and 5 are done it answers **Forbidden** to everyone.

### 4. Protect the stats page with Cloudflare Access

The same kind of login as old.reedcarnathangenealogy.org. In the Cloudflare dashboard (dash.cloudflare.com):

1. Left menu, under **Protect & connect**: **Zero Trust** > **Access controls** > **Applications**. Your
   old.reedcarnathangenealogy.org application is in the list, so you know you're in the right place.
2. **Create new application** > **Self-hosted**. The page that opens is "Create new self-hosted application".
3. **Destinations:** click **+ Add public hostname**. Subdomain `stats`, domain `kithgenealogy.org` (pick it
   from the list), path empty. If an empty **Private IP address** row is there too, leave it empty.
4. **Allow access through browser-based RDP, SSH, or VNC sessions:** leave it off.
5. **Access policies:** in **Add existing policy**, pick the policy you use for the old site (for David's
   account: **David and Pat**, which lets both of you in). For only you instead, click **Create new policy**:
   Action **Allow**, and an include rule of **Emails** with your address; then pick it in **Add existing policy**.
6. **Authentication:** leave **Accept all available identity providers** on (one-time PIN by email is fine).
7. Save at the bottom of the page.

Now get the two values for step 5. The quickest way is from the login redirect, once the application is saved:

    curl -s -o /dev/null -w '%{redirect_url}\n' https://stats.kithgenealogy.org/

It prints `https://TEAM.cloudflareaccess.com/cdn-cgi/access/login/stats.kithgenealogy.org?kid=AUD&...`:

- **Team name:** the `TEAM` part (for David's account: `reedweb`).
- **AUD tag:** the long hex string after `kid=`, up to the `&`.

(They're also in the dashboard: the AUD tag on the application's overview, the team name under Zero Trust >
Settings. Neither is a password; the login page shows both to anyone.)

### 5. Give the Worker the two values

They're kept by Cloudflare, not in this repo (the repo is public):

    npx wrangler@4 secret put ACCESS_TEAM

It asks "Enter a secret value": paste the team name (just `yourteam`) and press Enter. Then:

    npx wrangler@4 secret put ACCESS_AUD

Paste the AUD tag and press Enter. Each one takes effect straight away; no new deploy needed. Check both are set
(it shows the names, never the values):

    npx wrangler@4 secret list

### 6. Try it

Pretend to be a Kith site:

    curl -s https://check.kithgenealogy.org/v1/check -d '{"site":"00000000000000000000000000000000","version":"0.0.0"}'

It should print `{"latest":"0.2.0"}` (the newest release on GitHub). Now open https://stats.kithgenealogy.org in a
browser. Cloudflare asks for your email and sends a code; after that you see the page with **1** running site and
version 0.0.0. Remove the test row:

    npx wrangler@4 d1 execute kith-sites --remote --command "DELETE FROM sites WHERE version = '0.0.0'"

Reload the page: 0 sites. Real sites appear as they check in (each one once a day, between 04:00 and 05:00 their
time).

Last, in Git: commit the `database_id` change in `wrangler.toml`, so the next deploy uses the same database.
(Done for kithgenealogy.org: the database is `kith-sites`, 2026-11-03.)

## If something's wrong

- **Stats page says Forbidden after you logged in:** the team name or AUD tag is wrong. Get them again with the
  `curl` line in step 4 and repeat step 5. The team name must not include `.cloudflareaccess.com`.
- **Stats page opens without asking you to log in:** the Access application isn't covering it. Check its hostname
  is exactly `stats.kithgenealogy.org` (step 4). Until it is, the Worker still answers Forbidden.
- **curl prints `Bad request`:** the test line was mistyped; copy it again.
- **curl can't connect, or the browser shows a Cloudflare error page:** the custom domains can take a few minutes
  after the first deploy. Check Workers & Pages > `kith-check` > Settings > **Domains & Routes** lists both.
- **`{"latest":null}`:** the Worker couldn't read the newest GitHub release. It retries on later checks; Kith sites
  just don't show an update that day.
- **Watch requests live:** `npx wrangler@4 tail` (Ctrl+C to stop).

## Later

- **Change the Worker:** edit `src/index.js`, run `node --test test.mjs`, then `npx wrangler@4 deploy`.
- **See the raw numbers:** `npx wrangler@4 d1 execute kith-sites --remote --command "SELECT * FROM sites"`.
- **If someone inflates the count** by sending made-up IDs: Cloudflare dashboard > kithgenealogy.org > **Security**
  > **WAF** > **Rate limiting rules** > add a rule for hostname `check.kithgenealogy.org`, for example 10 requests
  a minute per IP, action Block. Then delete the bad rows, for example everything first seen on that day:
  `… --command "DELETE FROM sites WHERE first_seen = '2027-01-15'"`.
- **Turn it all off:** `npx wrangler@4 delete` removes the Worker and both addresses. Kith sites then just get no
  answer and carry on.
