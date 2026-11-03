"""Record the landing page tour (site/tour.mp4): a scripted walk through a fresh Kith, from importing the sample
family to tagging faces, with big captions. Run it through record.sh, which sets up the empty site it needs."""
import os
import re
import subprocess
import sys
from playwright.sync_api import sync_playwright

B, OUT, GED, PHOTO, COORDS, DB = sys.argv[1:7]
W, H = 1280, 800

# Captions and a soft cursor dot, drawn into each page so they're part of the recording.
OVERLAY = """
(() => {
  if (document.getElementById('tour-style')) return;
  const s = document.createElement('style'); s.id = 'tour-style';
  s.textContent = `
    @font-face { font-family: TourSerif; src: url(/static/fonts/fraunces-latin-wght-normal.woff2); font-weight: 100 900; }
    #tour-cap { pointer-events: none; position: fixed; left: 50%; bottom: 44px; transform: translate(-50%, 20px); z-index: 99999;
      background: rgba(28,20,13,.92); color: #f3e8d4; padding: 16px 30px; border-radius: 999px;
      font: 600 30px/1.1 TourSerif, Georgia, serif; letter-spacing: -.01em; white-space: nowrap;
      box-shadow: 0 18px 50px rgba(0,0,0,.45); opacity: 0; transition: opacity .35s, transform .35s; }
    #tour-cap.on { opacity: 1; transform: translate(-50%, 0); }
    #tour-cap b { color: #e3a66a; font-weight: 600; }
    #tour-card { position: fixed; inset: 0; z-index: 100000; display: grid; place-items: center; text-align: center;
      background: radial-gradient(ellipse 80% 60% at 70% 0%, #3b2716 0%, transparent 70%), #1c140d; color: #f3e8d4;
      font-family: TourSerif, Georgia, serif; transition: opacity .5s; }
    #tour-card h1 { color: #f3e8d4; font-size: 92px; font-weight: 500; margin: 0 0 10px; }
    #tour-card p { font-size: 40px; margin: 0; color: #bca88a; }
    #tour-card em { color: #e3a66a; }
    #tour-card small { display: block; margin-top: 36px; font: 600 26px Inter, system-ui, sans-serif; color: #e3a66a;
      letter-spacing: .04em; }
    #tour-dot { position: fixed; width: 26px; height: 26px; margin: -13px 0 0 -13px; border-radius: 50%;
      background: rgba(227,166,106,.55); border: 2px solid #a8431a; z-index: 100001; pointer-events: none;
      transition: transform .15s; left: -40px; top: -40px; }
    #tour-dot.down { transform: scale(.7); }`;
  document.head.appendChild(s);
  const cap = document.createElement('div'); cap.id = 'tour-cap'; document.body.appendChild(cap);
  const dot = document.createElement('div'); dot.id = 'tour-dot'; document.body.appendChild(dot);
  addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; });
  addEventListener('mousedown', () => dot.classList.add('down'));
  addEventListener('mouseup', () => dot.classList.remove('down'));
})()
"""


def caption(pg, html):
    pg.evaluate(OVERLAY)
    pg.evaluate("""h => { const c = document.getElementById('tour-cap'); c.classList.remove('on');
      setTimeout(() => { c.innerHTML = h; c.classList.add('on'); }, 60); }""", html)


def card(pg, html, hold):
    pg.evaluate(OVERLAY)
    pg.evaluate("""h => { let c = document.getElementById('tour-card');
      if (!c) { c = document.createElement('div'); c.id = 'tour-card'; document.body.appendChild(c); }
      c.innerHTML = h; c.style.opacity = 1; }""", html)
    pg.wait_for_timeout(hold)


def go(pg, path):
    pg.goto(B + path)
    pg.wait_for_load_state("networkidle")
    pg.evaluate(OVERLAY)


POS = [640, 400]


def glide(pg, x, y, ms=600):
    """Move the cursor over about ms milliseconds, easing in and out."""
    n = max(8, ms // 20)
    x0, y0 = POS
    for i in range(1, n + 1):
        k = i / n
        e = 2 * k * k if k < .5 else 1 - (-2 * k + 2) ** 2 / 2
        pg.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
        pg.wait_for_timeout(ms / n)
    POS[:] = [x, y]


def scroll(pg, dy, ms=1400):
    pg.evaluate("""([dy, ms]) => new Promise(r => { const y0 = scrollY, t0 = performance.now();
      const f = t => { const k = Math.min(1, (t - t0) / ms), e = k < .5 ? 2*k*k : 1 - (-2*k + 2)**2 / 2;
        scrollTo(0, y0 + dy * e); k < 1 ? requestAnimationFrame(f) : r(); }; requestAnimationFrame(f); })""", [dy, ms])


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM"))
    login = browser.new_context(viewport={"width": W, "height": H})
    lp = login.new_page()
    lp.goto(B + "/login")
    lp.fill("input[name=username]", "ruth")
    lp.fill("input[name=password]", "screenshots-only-1")
    lp.click("button[type=submit], form button")
    lp.wait_for_load_state("networkidle")
    state = login.storage_state()
    login.close()

    ctx = browser.new_context(viewport={"width": W, "height": H}, storage_state=state,
                              record_video_dir=OUT, record_video_size={"width": W, "height": H})
    # Tour-only layout for the photo page: a smaller photo, with the tag form beside it, from the first paint
    ctx.add_init_script("""addEventListener('DOMContentLoaded', () => { const st = document.createElement('style');
      st.textContent = '.face-photo { width: fit-content; } .face-photo img { max-height: 500px; width: auto; } .face-form:not([hidden]) { position: fixed; left: 830px; top: 150px; width: 400px; z-index: 50; box-shadow: 0 20px 60px rgba(0,0,0,.35); }'; document.head.appendChild(st); });""")
    pg = ctx.new_page()

    go(pg, "/")
    card(pg, "<div><h1>Kith</h1><p>Your family's story, on a website <em>you own.</em></p></div>", 2400)
    pg.evaluate("(c => { c.style.opacity = 0; c.style.pointerEvents = 'none' })(document.getElementById('tour-card'))")
    pg.wait_for_timeout(400)

    # An empty site: start typing people in...
    go(pg, "/edit/person/new")
    caption(pg, "Start from <b>scratch</b>...")
    for name, text in (("firstname", "George"), ("lastname", "Hollis")):
        f = pg.locator(f"input[name={name}]").first
        fb = f.bounding_box()
        glide(pg, fb["x"] + 40, fb["y"] + fb["height"] / 2, 300)
        f.click()
        f.press_sequentially(text, delay=70)
    pg.wait_for_timeout(500)

    # ...or bring a whole tree in from any genealogy program
    go(pg, "/admin/import")
    caption(pg, "...or bring your tree in <b>one GEDCOM file</b>")
    pg.set_input_files("input[name=file]", GED)
    btn = pg.get_by_role("button", name="Look at the file")
    bb = btn.bounding_box()
    glide(pg, bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, 450)
    pg.wait_for_timeout(300)
    btn.click()
    pg.wait_for_load_state("networkidle")
    pg.evaluate(OVERLAY)
    caption(pg, "Check it first. <b>Undo it any time.</b>")
    pg.wait_for_timeout(1300)
    btn = pg.get_by_role("button", name="Import them")
    bb = btn.bounding_box()
    glide(pg, bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, 450)
    btn.click()
    pg.wait_for_load_state("networkidle")
    pg.evaluate(OVERLAY)
    # Map coordinates for the sample's places, as the nightly geocode job would find them (off camera)
    subprocess.run(["docker", "compose", "exec", "-T", "db", "mariadb", "-uroot", "-pdev", DB],
                   stdin=open(COORDS), check=True)
    caption(pg, "<b>805 people</b>, ready to explore")
    pg.wait_for_timeout(1300)

    # Search for a person, the way a relative would
    go(pg, "/")
    caption(pg, "Find anyone in <b>seconds</b>")
    box = pg.locator("header input[name=q]").first
    bb = box.bounding_box()
    glide(pg, bb["x"] + 60, bb["y"] + bb["height"] / 2, 600)
    box.click()
    box.press_sequentially("George Hol", delay=80)
    pg.wait_for_timeout(800)
    result = pg.locator("#header-search-list a, #header-search-list [role=option]").first
    if result.count():
        rb = result.bounding_box()
        glide(pg, rb["x"] + 80, rb["y"] + rb["height"] / 2, 300)
        result.click()
    else:
        box.press("Enter")
    pg.wait_for_load_state("networkidle")
    pid = pg.url.rstrip("/").split("/")[-1]
    pg.evaluate(OVERLAY)
    caption(pg, "Every life, <b>one page</b>")
    pg.wait_for_timeout(1100)
    scroll(pg, 480, 1300)
    pg.wait_for_timeout(500)

    # Off camera: upload an old photo of George and Susan, so it can be tagged on camera
    helper = browser.new_context(storage_state=ctx.storage_state()).new_page()
    helper.goto(f"{B}/edit/media/new?for={pid}")
    helper.set_input_files("input[name=file]", PHOTO)
    helper.fill("input[name=description]", "George and Susan, about 1889")
    helper.get_by_role("button", name="Upload").click()
    helper.wait_for_load_state("networkidle")
    mid = re.search(r"/media/(\d+)", helper.content()).group(1)
    helper.context.close()

    go(pg, f"/media/{mid}")
    # A smaller photo, so the photo and the tag form below it fit on screen together
    caption(pg, "Tag faces in <b>old photos</b>")
    pg.wait_for_timeout(1000)
    for n, ((x0, y0, x1, y1), who) in enumerate((((.14, .17, .36, .60), "George Hollis"), ((.66, .27, .86, .70), "Susan Quayle"))):
        tag = pg.locator("#tag-face")
        tb = tag.bounding_box()
        glide(pg, tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2, 700)
        pg.wait_for_timeout(250)
        tag.click()
        pg.evaluate("scrollTo(0, 0)")
        if n == 0:
            caption(pg, "Draw a box around <b>a face</b>")
        pg.wait_for_timeout(700)
        img = pg.locator(".face-photo img").bounding_box()
        at = lambda fx, fy: (img["x"] + fx * img["width"], img["y"] + fy * img["height"])
        glide(pg, *at(x0, y0), 800)
        pg.wait_for_timeout(300)
        pg.mouse.down()
        glide(pg, *at(x1, y1), 1300)
        pg.mouse.up()
        if n == 0:
            caption(pg, "...then pick <b>who it is</b>")
        pg.wait_for_timeout(1000)
        q = pg.locator(".face-form button[name=quick]", has_text=who).first
        qb = q.bounding_box()
        glide(pg, qb["x"] + qb["width"] / 2, qb["y"] + qb["height"] / 2, 800)
        pg.wait_for_timeout(600)
        q.click()
        pg.wait_for_load_state("networkidle")
        pg.evaluate(OVERLAY)
        pg.evaluate("scrollTo(0, 0)")
        caption(pg, f"Tagged: <b>{who}</b>")
        box = pg.locator(".face-photo .face").nth(n).bounding_box()
        glide(pg, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, 700)
        pg.wait_for_timeout(1300)
    glide(pg, 640, 120, 500)
    pg.wait_for_timeout(400)

    go(pg, f"/person/{pid}")
    caption(pg, "Each face becomes <b>their portrait</b>")
    pg.wait_for_timeout(1800)

    # Hourglass from the person page
    link = pg.locator(f"a[href='/hourglass/{pid}']").first
    lb = link.bounding_box()
    glide(pg, lb["x"] + lb["width"] / 2, lb["y"] + lb["height"] / 2)
    pg.mouse.down(); pg.mouse.up()
    go(pg, f"/hourglass/{pid}?gens=1")
    pg.evaluate("""pid => { const a = [...document.querySelectorAll('main a[href="/person/' + pid + '"]')].pop();
      a && a.scrollIntoView({block: 'center', inline: 'center'}); }""", pid)
    caption(pg, "Parents above, <b>children below</b>")
    glide(pg, 640, 470, 750)
    pg.wait_for_timeout(2200)

    go(pg, f"/relationship?a={pid}&b=I150")
    caption(pg, "How are we related? <b>Shown face by face</b>")
    scroll(pg, 160, 900)
    pg.wait_for_timeout(2400)

    go(pg, "/places")
    caption(pg, "Places, from <b>state to town</b>")
    pa = pg.locator("main a[href='/places?within=PA']").first
    pb = pa.bounding_box()
    glide(pg, pb["x"] + pb["width"] / 2, pb["y"] + pb["height"] / 2, 700)
    pg.wait_for_timeout(300)
    pa.click()
    pg.wait_for_load_state("networkidle")
    pg.evaluate(OVERLAY)
    caption(pg, "Places, from <b>state to town</b>")
    pg.wait_for_timeout(1400)

    go(pg, "/map?surname=Hollis")
    caption(pg, "Map where a family <b>lived and moved</b>")
    pg.evaluate("document.querySelector('.leaflet-container').scrollIntoView({block: 'center'})")
    pg.wait_for_timeout(1200)
    pin = pg.locator(".leaflet-marker-icon, path.leaflet-interactive").first
    if pin.count():
        mb = pin.bounding_box()
        glide(pg, mb["x"] + mb["width"] / 2, mb["y"] + mb["height"] / 2, 900)
        pg.wait_for_timeout(300)
        pin.click(force=True)
    pg.wait_for_timeout(2400)

    go(pg, "/census/1910")
    caption(pg, "The census, <b>sorted by household</b>")
    pg.wait_for_timeout(900)
    scroll(pg, 420, 1800)
    pg.wait_for_timeout(1200)

    go(pg, f"/person/{pid}")
    caption(pg, "Light, dark, <b>or large print</b>")
    pg.wait_for_timeout(1000)
    pg.add_style_tag(content="*, *::before { transition: background-color .7s, color .7s, border-color .7s !important; }")
    pg.evaluate("document.documentElement.dataset.scheme = 'dark'")
    pg.wait_for_timeout(2000)

    card(pg, "<div><h1>Kith</h1><p>Free. Open source. <em>Yours.</em></p><small>kithgenealogy.org</small></div>", 2600)
    ctx.close()
    print(pg.video.path())
    browser.close()
