"""Record a Playwright page at its full device-pixel size with Chrome's screencast (Playwright's own video records
at CSS size), then encode the frames to H.264. Frames arrive only when the screen changes, so each lasts until the
next one."""
import base64
import os
import subprocess
import tempfile
import time


class Screencast:
    def __init__(self, page, width, height):
        self.dir = tempfile.mkdtemp(prefix="kith-cast-")
        self.frames = []  # (seconds since start, path)
        self.t0 = None
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": width, "maxHeight": height})
        self.size = (width, height)

    def _frame(self, f):
        now = f["metadata"].get("timestamp") or time.time()  # when Chrome drew it, not when it reached us
        if self.t0 is None:
            self.t0 = now
        path = os.path.join(self.dir, f"{len(self.frames):06d}.jpg")
        with open(path, "wb") as out:
            out.write(base64.b64decode(f["data"]))
        self.frames.append((now - self.t0, path))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": f["sessionId"]})

    def now(self):
        """Seconds since the first frame, for lining up narration."""
        return time.time() - self.t0 if self.t0 else 0.0

    def stop(self, out, skip=0.0):
        end = self.now()
        self.cdp.send("Page.stopScreencast")
        listing = os.path.join(self.dir, "list.txt")
        with open(listing, "w") as f:
            for i, (t, path) in enumerate(self.frames):
                nxt = self.frames[i + 1][0] if i + 1 < len(self.frames) else end
                f.write(f"file '{path}'\nduration {max(nxt - t, 0.001):.4f}\n")
            f.write(f"file '{self.frames[-1][1]}'\n")
        w, h = self.size
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-ss", f"{skip}",
                        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                               "fps=30,format=yuv420p",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-movflags", "+faststart", "-an", out],
                       check=True)
        return end
