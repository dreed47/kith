"""Record an upright (1080x1920) TikTok clip of Kith on a phone, with big captions, on the promo site from
site.sh sample. Usage: tiktok.py OUT.mp4"""
import os
import re
import sys
from playwright.sync_api import sync_playwright
from screencast import Screencast

B = "http://127.0.0.1:5079"
OUT = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
PHOTO = os.path.join(HERE, "..", "tour", "portrait-1889.jpg")
W, H, S = 432, 768, 2.5  # CSS viewport and scale: 1080x1920 frames
PID = "I32"  # George Hollis in the sample family

# Captions sit over the header, below TikTok's top tabs; TikTok also covers the bottom quarter and the right side.
OVERLAY = """
(() => {
  if (document.getElementById('tt-style')) return;
  const s = document.createElement('style'); s.id = 'tt-style';
  s.textContent = `
    @font-face { font-family: TTSerif; src: url(/static/fonts/fraunces-latin-wght-normal.woff2); font-weight: 100 900; }
    #tt-cap { pointer-events: none; position: fixed; left: 6%; right: 16%; top: 9%; z-index: 99999; text-align: center;
      background: rgba(28,20,13,.93); color: #f3e8d4; padding: 14px 18px; border-radius: 22px;
      font: 600 27px/1.15 TTSerif, Georgia, serif; box-shadow: 0 18px 50px rgba(0,0,0,.45);
      opacity: 0; transform: translateY(14px); transition: opacity .3s, transform .3s; }
    #tt-cap.on { opacity: 1; transform: none; }
    #tt-cap b { color: #e3a66a; font-weight: 600; }
    #tt-card { position: fixed; inset: 0; z-index: 100000; display: grid; place-items: center; text-align: center;
      padding: 0 36px 0 28px; background: radial-gradient(ellipse 90% 50% at 70% 0%, #3b2716 0%, transparent 70%), #1c140d;
      color: #f3e8d4; font-family: TTSerif, Georgia, serif; transition: opacity .5s; }
    #tt-card h1 { color: #f3e8d4; font-size: 44px; line-height: 1.1; font-weight: 500; margin: 0 0 18px; }
    #tt-card p { font-size: 25px; line-height: 1.3; margin: 0; color: #bca88a; }
    #tt-card em { color: #e3a66a; font-style: normal; }
    #tt-card small { display: block; margin-top: 30px; font: 600 21px Inter, system-ui, sans-serif; color: #e3a66a; }
    #tt-dot { position: fixed; width: 34px; height: 34px; margin: -17px 0 0 -17px; border-radius: 50%;
      background: rgba(227,166,106,.5); border: 2px solid #a8431a; z-index: 100001; pointer-events: none;
      transition: transform .15s; left: -60px; top: -60px; }
    #tt-dot.down { transform: scale(.7); }`;
  document.head.appendChild(s);
  for (const id of ['tt-cap', 'tt-dot']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  const dot = document.getElementById('tt-dot');
  addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; });
  addEventListener('mousedown', () => dot.classList.add('down'));
  addEventListener('mouseup', () => dot.classList.remove('down'));
})()
"""
POS = [W / 2, H / 2]


def caption(pg, html):
    pg.evaluate(OVERLAY)
    pg.evaluate("""h => { const c = document.getElementById('tt-cap'); c.classList.remove('on');
      setTimeout(() => { c.innerHTML = h; c.classList.add('on'); }, 60); }""", html)


def card(pg, html, hold):
    pg.evaluate(OVERLAY)
    pg.evaluate("""h => { let c = document.getElementById('tt-card');
      if (!c) { c = document.createElement('div'); c.id = 'tt-card'; document.body.appendChild(c); }
      c.innerHTML = h; c.style.opacity = 1; }""", html)
    pg.wait_for_timeout(hold)


def go(pg, path):
    pg.goto(B + path)
    pg.wait_for_load_state("networkidle")
    pg.evaluate(OVERLAY)


def glide(pg, x, y, ms=500):
    n = max(8, ms // 20)
    x0, y0 = POS
    for i in range(1, n + 1):
        k = i / n
        e = 2 * k * k if k < .5 else 1 - (-2 * k + 2) ** 2 / 2
        pg.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
        pg.wait_for_timeout(ms / n)
    POS[:] = [x, y]


def tap(pg, loc, ms=500, force=False):
    bb = loc.bounding_box()
    glide(pg, bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, ms)
    pg.wait_for_timeout(200)
    loc.click(force=force)


def scroll(pg, dy, ms=1200):
    pg.evaluate("""([dy, ms]) => new Promise(r => { const y0 = scrollY, t0 = performance.now();
      const f = t => { const k = Math.min(1, (t - t0) / ms), e = k < .5 ? 2*k*k : 1 - (-2*k + 2)**2 / 2;
        scrollTo(0, y0 + dy * e); k < 1 ? requestAnimationFrame(f) : r(); }; requestAnimationFrame(f); })""", [dy, ms])


def login(browser, **kw):
    ctx = browser.new_context(**kw)
    pg = ctx.new_page()
    pg.goto(B + "/login")
    pg.fill("input[name=username]", "ruth")
    pg.fill("input[name=password]", "screenshots-only-1")
    pg.click("form button")
    pg.wait_for_load_state("networkidle")
    return ctx, pg


def tagged_photo(browser):
    """Off camera: upload the 1889 photo for George and tag George and Susan. Returns its media id."""
    ctx, pg = login(browser, viewport={"width": 1280, "height": 2400})
    pg.goto(f"{B}/edit/media/new?for={PID}")
    pg.set_input_files("input[name=file]", PHOTO)
    pg.fill("input[name=description]", "George and Susan, about 1889")
    pg.get_by_role("button", name="Upload").click()
    pg.wait_for_load_state("networkidle")
    mid = re.search(r"/media/(\d+)", pg.content()).group(1)
    pg.goto(f"{B}/media/{mid}")
    for (x0, y0, x1, y1), who in (((.14, .17, .36, .60), "George Hollis"), ((.66, .27, .86, .70), "Susan Quayle")):
        pg.click("#tag-face")
        img = pg.locator(".face-photo img").bounding_box()
        pg.mouse.move(img["x"] + x0 * img["width"], img["y"] + y0 * img["height"])
        pg.mouse.down()
        pg.mouse.move(img["x"] + x1 * img["width"], img["y"] + y1 * img["height"], steps=10)
        pg.mouse.up()
        pg.locator(".face-form button[name=quick]", has_text=who).first.click()
        pg.wait_for_load_state("networkidle")
    ctx.close()
    return mid


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"))
    mid = tagged_photo(browser)
    ctx, pg = login(browser, viewport={"width": W, "height": H}, device_scale_factor=S, is_mobile=True, has_touch=True)
    cast = Screencast(pg, 1080, 1920)

    go(pg, "/")
    card(pg, "<div><h1>Stop paying every month for your family tree</h1>"
             "<p>Host it yourself. <em>Free and open source.</em></p></div>", 3000)
    pg.evaluate("(c => { c.style.opacity = 0; c.style.pointerEvents = 'none' })(document.getElementById('tt-card'))")
    pg.wait_for_timeout(300)

    caption(pg, "Your whole family, <b>on your own server</b>")
    box = pg.locator("main input[name=q]").first
    tap(pg, box, 600)
    box.press_sequentially("George Hollis", delay=70)
    pg.wait_for_timeout(300)
    box.press("Enter")
    pg.wait_for_load_state("networkidle")
    go(pg, f"/person/{PID}")
    caption(pg, "Every ancestor gets <b>a page</b>")
    pg.wait_for_timeout(1500)
    scroll(pg, 380, 1400)
    pg.wait_for_timeout(700)

    go(pg, f"/media/{mid}")
    caption(pg, "Tag faces in <b>old photos</b>")
    pg.wait_for_timeout(900)
    face = pg.locator(".face-photo .face").first
    if face.count():
        fb = face.bounding_box()
        glide(pg, fb["x"] + fb["width"] / 2, fb["y"] + fb["height"] / 2, 700)
    pg.wait_for_timeout(1600)

    go(pg, f"/person/{PID}")
    caption(pg, "...and they become <b>portraits</b>")
    pg.wait_for_timeout(2000)

    go(pg, f"/hourglass/{PID}?gens=1")
    caption(pg, "Parents above, <b>children below</b>")
    pg.wait_for_timeout(1200)
    scroll(pg, 300, 1300)
    pg.wait_for_timeout(900)

    go(pg, "/map?surname=Hollis")
    caption(pg, "See where they <b>lived and moved</b>")
    pg.evaluate("document.querySelector('.leaflet-container').scrollIntoView({block: 'end'})")
    pg.wait_for_timeout(1000)
    pin = pg.locator(".leaflet-marker-icon, path.leaflet-interactive").first
    if pin.count():
        tap(pg, pin, 700, force=True)
    pg.wait_for_timeout(1800)

    go(pg, "/census/1910")
    caption(pg, "The census, <b>by household</b>")
    pg.wait_for_timeout(900)
    scroll(pg, 500, 1600)
    pg.wait_for_timeout(700)

    go(pg, f"/person/{PID}")
    caption(pg, "Light, dark, <b>or large print</b>")
    pg.wait_for_timeout(900)
    pg.add_style_tag(content="*, *::before { transition: background-color .7s, color .7s, border-color .7s !important; }")
    pg.evaluate("document.documentElement.dataset.scheme = 'dark'")
    pg.wait_for_timeout(1800)

    caption(pg, "Living relatives <b>stay private</b>")
    pg.wait_for_timeout(1800)

    card(pg, "<div><h1>Kith</h1><p>Your family's story, on a website <em>you own.</em></p>"
             "<small>kithgenealogy.org</small></div>", 3200)
    cast.stop(OUT, skip=0.3)
    ctx.close()
    browser.close()
print(OUT)
