"""Record the narrated YouTube walkthrough (1920x1080) on an EMPTY promo site (site.sh, no "sample"): each scene runs
while its narration (VOICEDIR/<scene>.wav, from voice.py) plays, then the narration is mixed in, over MUSIC if given
(looped, quiet, and dipping further under the voice).
Usage: youtube.py VOICEDIR OUT.mp4 [MUSIC]"""
import os
import re
import subprocess
import sys
import tempfile
import soundfile as sf
from playwright.sync_api import sync_playwright
from screencast import Screencast

B = "http://127.0.0.1:5079"
VOICE, OUT = sys.argv[1:3]
MUSIC = sys.argv[3] if len(sys.argv) > 3 else None
HERE = os.path.dirname(os.path.abspath(__file__))
KITH = os.path.join(HERE, "..", "..")
TOUR = os.path.join(HERE, "..", "tour")
W, H = 1280, 720
PID = "I32"  # George Hollis in the sample family
DEFAULT_ORDER = ("intro install import search person faces portrait charts relationship places map census privacy "
                 "relatives themes outro").split()

OVERLAY = """
(() => {
  if (document.getElementById('yt-style')) return;
  const s = document.createElement('style'); s.id = 'yt-style';
  s.textContent = `
    @font-face { font-family: YTSerif; src: url(https://kithgenealogy.org/fonts/fraunces-latin-wght-normal.woff2); font-weight: 100 900; }
    #yt-cap { pointer-events: none; position: fixed; left: 50%; bottom: 36px; transform: translate(-50%, 16px); z-index: 99999;
      background: rgba(28,20,13,.92); color: #f3e8d4; padding: 12px 26px; border-radius: 999px;
      font: 600 26px/1.1 YTSerif, Georgia, serif; white-space: nowrap; box-shadow: 0 18px 50px rgba(0,0,0,.45);
      opacity: 0; transition: opacity .35s, transform .35s; }
    #yt-cap.on { opacity: 1; transform: translate(-50%, 0); }
    #yt-cap b { color: #e3a66a; font-weight: 600; }
    #yt-card { position: fixed; inset: 0; z-index: 100000; display: grid; place-items: center; text-align: center;
      background: radial-gradient(ellipse 80% 60% at 70% 0%, #3b2716 0%, transparent 70%), #1c140d; color: #f3e8d4;
      font-family: YTSerif, Georgia, serif; transition: opacity .6s; }
    #yt-card h1 { color: #f3e8d4; font-size: 84px; font-weight: 500; margin: 0 0 12px; }
    #yt-card p { font-size: 34px; margin: 0; color: #bca88a; }
    #yt-card em { color: #e3a66a; font-style: normal; }
    #yt-card small { display: block; margin-top: 34px; font: 600 24px Inter, system-ui, sans-serif; color: #e3a66a; }
    #yt-dot { position: fixed; width: 24px; height: 24px; margin: -12px 0 0 -12px; border-radius: 50%;
      background: rgba(227,166,106,.55); border: 2px solid #a8431a; z-index: 100001; pointer-events: none;
      transition: transform .15s; left: -40px; top: -40px; }
    #yt-dot.down { transform: scale(.7); }`;
  document.head.appendChild(s);
  for (const id of ['yt-cap', 'yt-dot']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  const dot = document.getElementById('yt-dot');
  addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; });
  addEventListener('mousedown', () => dot.classList.add('down'));
  addEventListener('mouseup', () => dot.classList.remove('down'));
})()
"""
POS = [W / 2, H / 2]


def caption(pg, html):
    pg.evaluate(OVERLAY)
    pg.evaluate("""h => { const c = document.getElementById('yt-cap'); c.classList.remove('on');
      if (h) setTimeout(() => { c.innerHTML = h; c.classList.add('on'); }, 60); }""", html)


def card(pg, html):
    pg.evaluate(OVERLAY)
    pg.evaluate("""h => { let c = document.getElementById('yt-card');
      if (!c) { c = document.createElement('div'); c.id = 'yt-card'; document.body.appendChild(c); }
      c.innerHTML = h; c.style.opacity = 1; document.getElementById('yt-dot').style.display = 'none'; }""", html)


def go(pg, url):
    pg.goto(url if url.startswith("http") else B + url)
    pg.wait_for_load_state("networkidle")
    pg.evaluate(OVERLAY)
    pg.mouse.move(*POS)


def glide(pg, x, y, ms=600):
    n = max(8, int(ms) // 20)
    x0, y0 = POS
    for i in range(1, n + 1):
        k = i / n
        e = 2 * k * k if k < .5 else 1 - (-2 * k + 2) ** 2 / 2
        pg.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
        pg.wait_for_timeout(ms / n)
    POS[:] = [x, y]


def point(pg, loc, ms=600):
    bb = loc.bounding_box()
    glide(pg, bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, ms)
    return loc


def click(pg, loc, ms=600, force=False):
    point(pg, loc, ms)
    pg.wait_for_timeout(250)
    loc.click(force=force)
    pg.wait_for_load_state("networkidle")
    pg.evaluate(OVERLAY)


def scroll(pg, dy, ms=1400):
    pg.evaluate("""([dy, ms]) => new Promise(r => { const y0 = scrollY, t0 = performance.now();
      const f = t => { const k = Math.min(1, (t - t0) / ms), e = k < .5 ? 2*k*k : 1 - (-2*k + 2)**2 / 2;
        scrollTo(0, y0 + dy * e); k < 1 ? requestAnimationFrame(f) : r(); }; requestAnimationFrame(f); })""", [dy, ms])


def log_in(pg):
    pg.fill("input[name=username]", "ruth")
    pg.fill("input[name=password]", "screenshots-only-1")
    pg.click("form button")
    pg.wait_for_load_state("networkidle")


# Each scene starts with its narration and lasts until it ends (plus a breath)
starts = {}


class Scene:
    def __init__(self, cast, pg, name):
        self.cast, self.pg, self.name = cast, pg, name
        info = sf.info(f"{VOICE}/{name}.wav")
        self.length = info.frames / info.samplerate

    def __enter__(self):
        starts[self.name] = self.t0 = self.cast.now()
        return self

    def left(self):
        return self.t0 + self.length - self.cast.now()

    def __exit__(self, *exc):
        if exc[0] is None:
            self.pg.wait_for_timeout(max(0, self.left() + 0.8) * 1000)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"))
    ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1.5)
    # The photo page as in the landing-page tour: a smaller photo, with the tag form beside it
    ctx.add_init_script("""addEventListener('DOMContentLoaded', () => { const st = document.createElement('style');
      st.textContent = '.face-photo { width: fit-content; } .face-photo img { max-height: 470px; width: auto; } .face-form:not([hidden]) { position: fixed; left: 830px; top: 130px; width: 400px; z-index: 50; box-shadow: 0 20px 60px rgba(0,0,0,.35); }'; document.head.appendChild(st); });""")
    pg = ctx.new_page()
    go(pg, "/login")
    log_in(pg)
    go(pg, "/")
    card(pg, "<div><h1>Kith</h1><p>A family history website <em>you own.</em></p></div>")
    pg.wait_for_timeout(500)
    cast = Screencast(pg, 1920, 1080)
    pg.wait_for_timeout(1200)  # a quiet start before the voice

    def scene_intro(s):
        pg.wait_for_timeout(s.length * 600)
        pg.evaluate("(c => { c.innerHTML = '<div><h1>Kith</h1><p>Free. Open source. <em>Yours.</em></p></div>' })"
                    "(document.getElementById('yt-card'))")

    def scene_install(s):
        go(pg, "https://kithgenealogy.org/")
        pg.wait_for_timeout(2500)
        y = pg.evaluate("document.getElementById('install').getBoundingClientRect().top + scrollY - 40")
        scroll(pg, y, 2200)
        caption(pg, "One command on <b>Proxmox</b>")

    def scene_import(s):
        go(pg, "/edit/person/new")
        caption(pg, "Start from <b>scratch</b>...")
        for name, text in (("firstname", "George"), ("lastname", "Hollis")):
            f = pg.locator(f"input[name={name}]").first
            point(pg, f, 400)
            f.click()
            f.press_sequentially(text, delay=80)
        pg.wait_for_timeout(1500)
        go(pg, "/admin/import")
        caption(pg, "...or import a <b>GEDCOM file</b>")
        pg.wait_for_timeout(2500)
        scroll(pg, 260, 1500)
        pg.wait_for_timeout(1500)
        scroll(pg, -260, 900)
        pg.set_input_files("input[name=file]", os.path.join(KITH, "app", "sample.ged"))
        click(pg, pg.get_by_role("button", name="Look at the file"))
        caption(pg, "See what's in it <b>first</b>")
        pg.wait_for_timeout(1500)
        scroll(pg, 300, 1600)
        pg.wait_for_timeout(1200)
        btn = pg.get_by_role("button", name="Import them")
        btn.scroll_into_view_if_needed()
        click(pg, btn)
        subprocess.run(["docker", "compose", "exec", "-T", "db", "mariadb", "-uroot", "-pdev", "kith_promo"],
                       stdin=open(os.path.join(TOUR, "coordinates.sql")), check=True, cwd=KITH)
        caption(pg, "<b>805 people</b>, and undo any time")
        # Off camera: the 1889 photo of George and Susan, to tag on camera
        helper = browser.new_context(storage_state=ctx.storage_state()).new_page()
        helper.goto(f"{B}/edit/media/new?for={PID}")
        helper.set_input_files("input[name=file]", os.path.join(TOUR, "portrait-1889.jpg"))
        helper.fill("input[name=description]", "George and Susan, about 1889")
        helper.get_by_role("button", name="Upload").click()
        helper.wait_for_load_state("networkidle")
        global mid
        mid = re.search(r"/media/(\d+)", helper.content()).group(1)
        helper.context.close()

    def scene_search(s):
        go(pg, "/")
        caption(pg, "Find anyone in <b>seconds</b>")
        box = pg.locator("header input[name=q]").first
        point(pg, box, 700)
        box.click()
        box.press_sequentially("George Hol", delay=110)
        pg.wait_for_timeout(1200)
        result = pg.locator("#header-search-list a, #header-search-list [role=option]").first
        pg.wait_for_timeout(max(0, s.left() - 1.5) * 1000)
        if result.count():
            click(pg, result, 400)
        else:
            box.press("Enter")

    def scene_person(s):
        go(pg, f"/person/{PID}")
        caption(pg, "Every life, <b>one page</b>")
        pg.wait_for_timeout(2500)
        caption(pg, "")
        scroll(pg, 500, 3500)
        pg.wait_for_timeout(1500)
        scroll(pg, 600, 3500)
        pg.wait_for_timeout(1200)
        scroll(pg, 700, 3500)

    def scene_faces(s):
        go(pg, f"/media/{mid}")
        caption(pg, "Tag faces in <b>old photos</b>")
        for n, ((x0, y0, x1, y1), who) in enumerate((((.14, .17, .36, .60), "George Hollis"),
                                                     ((.66, .27, .86, .70), "Susan Quayle"))):
            tag = pg.locator("#tag-face")
            point(pg, tag, 600)
            pg.wait_for_timeout(200)
            tag.click()
            pg.evaluate("scrollTo(0, 0)")
            pg.wait_for_timeout(400)
            img = pg.locator(".face-photo img").bounding_box()
            at = lambda fx, fy: (img["x"] + fx * img["width"], img["y"] + fy * img["height"])
            glide(pg, *at(x0, y0), 700)
            pg.mouse.down()
            glide(pg, *at(x1, y1), 1100)
            pg.mouse.up()
            pg.wait_for_timeout(600)
            click(pg, pg.locator(".face-form button[name=quick]", has_text=who).first, 700)
            pg.evaluate("scrollTo(0, 0)")
            caption(pg, f"Tagged: <b>{who}</b>")
            pg.wait_for_timeout(700)

    def scene_portrait(s):
        go(pg, f"/person/{PID}")
        caption(pg, "Faces become <b>portraits</b>")

    def scene_charts(s):
        third = s.length / 3 * 1000
        go(pg, f"/hourglass/{PID}?gens=1")
        pg.evaluate("""pid => { const a = [...document.querySelectorAll('main a[href="/person/' + pid + '"]')].pop();
          a && a.scrollIntoView({block: 'center', inline: 'center'}); }""", PID)
        caption(pg, "Hourglass: <b>parents above, children below</b>")
        pg.wait_for_timeout(third)
        go(pg, f"/pedigree/{PID}/fan")
        caption(pg, "Fan chart, <b>with photos</b>")
        scroll(pg, 120, 900)
        pg.wait_for_timeout(third - 900)
        go(pg, "/descendants/I1")
        caption(pg, "Descendants, <b>down the generations</b>")
        scroll(pg, 400, 3000)

    def scene_relationship(s):
        go(pg, f"/relationship?a={PID}&b=I150")
        caption(pg, "How are we related? <b>Face by face</b>")
        scroll(pg, 160, 900)

    def scene_places(s):
        go(pg, "/places")
        caption(pg, "From <b>state to town</b>")
        pg.wait_for_timeout(1200)
        click(pg, pg.locator("main a[href='/places?within=PA']").first, 700)
        caption(pg, "From <b>state to town</b>")
        pg.wait_for_timeout(1200)
        scroll(pg, 300, 1500)

    def scene_map(s):
        go(pg, "/map?surname=Hollis")
        caption(pg, "Where a family <b>lived and moved</b>")
        pg.evaluate("document.querySelector('.leaflet-container').scrollIntoView({block: 'center'})")
        pg.wait_for_timeout(1500)
        pin = pg.locator(".leaflet-marker-icon, path.leaflet-interactive").first
        if pin.count():
            click(pg, pin, 900, force=True)

    def scene_census(s):
        go(pg, "/census")
        caption(pg, "The census, <b>year by year</b>")
        pg.wait_for_timeout(2500)
        go(pg, "/census/1910")
        caption(pg, "Households, <b>by place</b>")
        pg.wait_for_timeout(1000)
        scroll(pg, 500, 3000)

    def scene_privacy(s):
        pg.evaluate("document.querySelector('.account-menu').open = true")
        pg.wait_for_timeout(1200)
        click(pg, pg.locator(".account-menu button", has_text="Log out"), 700)
        caption(pg, "Visitors see a <b>welcome page</b>")
        pg.wait_for_timeout(s.length * 450)
        go(pg, "/account/request")
        caption(pg, "Relatives <b>ask for an account</b>")
        pg.wait_for_timeout(max(0, s.left() - 1.2) * 1000)
        go(pg, "/login")
        log_in(pg)

    def scene_relatives(s):
        go(pg, f"/person/{PID}")
        caption(pg, "\"This is me\", <b>watching</b>, comments")
        pg.wait_for_timeout(1000)
        point(pg, pg.get_by_role("button", name="This is me"), 800)
        pg.wait_for_timeout(1800)
        point(pg, pg.locator("button", has_text="Watch").first, 900)

    def scene_themes(s):
        go(pg, "/account/")
        caption(pg, "Five themes, <b>your choice</b>")
        choices = pg.locator(".theme-choice")
        for i in range(choices.count()):
            c = choices.nth(i)
            c.scroll_into_view_if_needed()
            click(pg, c, 500)
            pg.wait_for_timeout(900)
        choices.first.click()

    def scene_outro(s):
        go(pg, "https://kithgenealogy.org/")
        tng = pg.locator("text=Coming from TNG").first
        if tng.count():
            y = pg.evaluate("e => e.getBoundingClientRect().top + scrollY - 200", tng.element_handle())
            scroll(pg, y, 2000)
        pg.wait_for_timeout(s.length * 450)
        card(pg, "<div><h1>Kith</h1><p>Free. Open source. <em>Yours.</em></p><small>kithgenealogy.org</small></div>")

    def scene_about(s):
        go(pg, "https://kithgenealogy.org/")
        caption(pg, "Built with AI, <b>run by a human</b>")
        pg.wait_for_timeout(1500)
        who = pg.locator("text=Who's behind it").first
        y = pg.evaluate("e => e.getBoundingClientRect().top + scrollY - 200", who.element_handle())
        scroll(pg, y, 2500)
        pg.wait_for_timeout(2500)
        caption(pg, "")
        pg.wait_for_timeout(max(0, s.length * 0.5 - 6.5) * 1000)
        go(pg, "https://github.com/dreed47/kith")
        pg.wait_for_timeout(1500)
        vet = pg.locator("article >> text=veteran").first
        if vet.count():
            y = pg.evaluate("e => e.getBoundingClientRect().top + scrollY - 250", vet.element_handle())
            scroll(pg, y, 3000)

    # Scenes run in the order of the narration's sections (order.txt from voice.py)
    order_file = f"{VOICE}/order.txt"
    order = open(order_file).read().split() if os.path.exists(order_file) else DEFAULT_ORDER
    for name in order:
        with Scene(cast, pg, name) as s:
            globals()["scene_" + name](s)
    pg.wait_for_timeout(2500)

    silent = tempfile.mktemp(suffix=".mp4")
    total = cast.stop(silent)
    browser.close()

# Mix each scene's narration in at its start
inputs, chains = [], []
for i, (name, t) in enumerate(starts.items()):
    inputs += ["-i", f"{VOICE}/{name}.wav"]
    chains.append(f"[{i + 1}:a]adelay={int(t * 1000)}:all=1[a{i}]")
mix = ";".join(chains) + ";" + "".join(f"[a{i}]" for i in range(len(starts))) + \
      f"amix=inputs={len(starts)}:normalize=0,apad,atrim=0:{total:.2f}"
if MUSIC:
    m = len(starts) + 1
    inputs += ["-stream_loop", "-1", "-i", MUSIC]
    mix += (f",asplit[v1][v2];[{m}:a]atrim=0:{total:.2f},volume=0.08,afade=t=in:d=2,"
            f"afade=t=out:st={total - 5:.2f}:d=5[m];[m][v2]sidechaincompress=threshold=0.02:ratio=6:attack=80:release=900[md];"
            "[v1][md]amix=inputs=2:normalize=0[a]")
else:
    mix += "[a]"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", silent, *inputs, "-filter_complex", mix, "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", OUT], check=True)
print(OUT, f"{total:.0f}s")
