#!/usr/bin/env python3
"""Wallpaper picker: browse a folder of images, filter to ones big enough for your screen.

Usage:  python3 wallfit.py [FOLDER] [--port 8765] [--no-browser] [--no-subfolders]

Starts a small local web server (localhost only) and opens it in your browser.
You can switch folders from inside the page. Thumbnails are cached in
~/.cache/wallfit so later runs are instant.
"""
import argparse
import hashlib
import hmac
import json
import mimetypes
import secrets
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
THUMB_SIZE = 480
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wallfit"
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".cache"}

Image.MAX_IMAGE_PIXELS = None  # allow very large wallpapers


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXTS


def thumb_path(p: Path) -> Path:
    st = p.stat()
    key = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()
    return CACHE_DIR / (hashlib.sha1(key).hexdigest() + ".jpg")


def _has_rotation(im) -> bool:
    try:
        return im.getexif().get(0x0112, 1) in (5, 6, 7, 8)
    except Exception:
        return False


def scan_one(p: Path, root: Path):
    try:
        with Image.open(p) as im:
            w, h = im.size
            if _has_rotation(im):
                w, h = h, w
            tp = thumb_path(p)
            if not tp.exists():
                im.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))  # fast JPEG decode
                t = ImageOps.exif_transpose(im)
                t.thumbnail((THUMB_SIZE, THUMB_SIZE))
                if t.mode not in ("RGB", "L"):
                    t = t.convert("RGBA")
                    bg = Image.new("RGB", t.size, (32, 32, 32))
                    bg.paste(t, mask=t.split()[-1])
                    t = bg
                t.save(tp, "JPEG", quality=82)
        return {
            "rel": str(p.relative_to(root)),
            "name": p.name,
            "w": w,
            "h": h,
            "bytes": p.stat().st_size,
            "thumb": tp.name,
        }
    except Exception as e:  # unreadable / corrupt file
        print(f"  skipped {p.name}: {e}", file=sys.stderr)
        return None


def list_images(root: Path, recursive: bool):
    if not recursive:
        return sorted(p for p in root.iterdir() if p.is_file() and is_image(p))
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        out += [Path(dirpath) / f for f in filenames if is_image(Path(f))]
    return sorted(out)


class Library:
    """The currently open folder. Scans run in a background thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.root = None
        self.recursive = True
        self.images = []
        self.allowed = set()
        self.scanning = False
        self.done = 0
        self.total = 0
        self.gen = 0  # bumps on each new scan so stale scans are discarded
        self.native_pick = None  # set by the desktop app: callable(start_dir) -> path | None
        self.raise_window = None  # set by the desktop app: callable() bringing its window forward

    def status(self):
        with self.lock:
            return {
                "folder": str(self.root) if self.root else None,
                "recursive": self.recursive,
                "scanning": self.scanning,
                "done": self.done,
                "total": self.total,
                "count": len(self.images),
                "native": self.native_pick is not None,
            }

    def open(self, root: Path, recursive: bool):
        with self.lock:
            self.gen += 1
            gen = self.gen
            self.root, self.recursive = root, recursive
            self.images, self.allowed = [], set()
            self.scanning, self.done, self.total = True, 0, 0
        threading.Thread(target=self._scan, args=(root, recursive, gen), daemon=True).start()

    def _scan(self, root, recursive, gen):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            files = list_images(root, recursive)
        except OSError as e:
            print(f"Cannot read {root}: {e}", file=sys.stderr)
            files = []
        with self.lock:
            if gen != self.gen:
                return
            self.total = len(files)
        print(f"Scanning {len(files)} images in {root} ...", flush=True)

        def job(p):
            r = scan_one(p, root)
            with self.lock:
                self.done += 1
            return r

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
            results = [r for r in ex.map(job, files) if r]
        with self.lock:
            if gen != self.gen:
                return
            self.images = results
            self.allowed = {i["rel"] for i in results}
            self.scanning = False
        print(f"  done: {len(results)} images", flush=True)

    def path_for(self, rel):
        with self.lock:
            return self.root / rel if rel in self.allowed else None


def browse(path: str):
    p = Path(path or Path.home()).expanduser()
    try:
        p = p.resolve()
        entries = sorted(os.scandir(p), key=lambda e: e.name.lower())
    except OSError as e:
        return {"error": str(e), "path": str(p)}
    dirs, n_img = [], 0
    for e in entries:
        try:
            if e.is_dir() and not e.name.startswith("."):
                dirs.append(e.name)
            elif e.is_file() and is_image(Path(e.name)):
                n_img += 1
        except OSError:
            pass
    return {
        "path": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "dirs": dirs,
        "images": n_img,
        "home": str(Path.home()),
    }


CROP_DIR = Path.home() / "Pictures" / "Wallpapers"
QDBUS = next((q for q in ("qdbus6", "qdbus-qt6", "qdbus") if shutil.which(q)), None)
# KDE "Positioning" options -> Plasma's FillMode values
FILL_MODES = {0: "Scaled", 1: "Scaled, keep proportions", 2: "Scaled and cropped", 3: "Tiled", 6: "Centered"}


def plasma_script(js: str):
    """Run a Plasma desktop script; returns (ok, printed output)."""
    if not QDBUS:
        return False, "qdbus not found"
    r = subprocess.run(
        [QDBUS, "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", js],
        capture_output=True, text=True, timeout=15,
    )
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def kde_screens():
    """Monitors as Plasma sees them, matched to output names/resolutions from kscreen-doctor."""
    ok, out = plasma_script("""
        var res = [], ds = desktops();
        for (var i = 0; i < ds.length; i++) {
          var d = ds[i], g = screenGeometry(d.screen);
          d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
          res.push({screen: d.screen, x: g.x, y: g.y, lw: g.width, lh: g.height,
                    plugin: d.wallpaperPlugin, image: d.readConfig("Image"), fill: d.readConfig("FillMode")});
        }
        print(JSON.stringify(res));""")
    if not ok:
        return None
    try:
        desks = json.loads(out)
    except ValueError:
        return None
    outputs = []
    if shutil.which("kscreen-doctor"):
        try:
            r = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=10)
            outputs = [o for o in json.loads(r.stdout).get("outputs", []) if o.get("enabled") and o.get("connected")]
        except Exception:
            pass
    screens = {}
    for d in desks:  # one entry per screen (several if there are multiple activities)
        if d["screen"] in screens:
            continue
        o = next((o for o in outputs if o["pos"]["x"] == d["x"] and o["pos"]["y"] == d["y"]), None)
        w, h = (o["size"]["width"], o["size"]["height"]) if o else (round(d["lw"]), round(d["lh"]))
        screens[d["screen"]] = {
            "screen": d["screen"], "name": o["name"] if o else f"Screen {d['screen'] + 1}",
            "w": w, "h": h, "x": d["x"], "y": d["y"], "lw": d["lw"], "lh": d["lh"],
            "primary": bool(o and o.get("priority") == 1),
            "image": d["image"], "fill": int(d["fill"]) if str(d["fill"]).isdigit() else 2,
        }
    return sorted(screens.values(), key=lambda s: (s["x"], s["y"]))


def make_crop(src: Path, box):
    """Save a cropped copy of src (box = x, y, w, h in image pixels) and return its path."""
    x, y, w, h = (int(round(float(box[k]))) for k in ("x", "y", "w", "h"))
    CROP_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        x, y = max(0, x), max(0, y)
        w, h = min(w, im.width - x), min(h, im.height - y)
        out = im.crop((x, y, x + w, y + h))
        png = src.suffix.lower() == ".png"
        dest = CROP_DIR / f"{src.stem}_crop_{x}_{y}_{w}x{h}.{'png' if png else 'jpg'}"
        if png:
            out.save(dest, "PNG")
        else:
            out.convert("RGB").save(dest, "JPEG", quality=95)
    return dest


def set_kde_wallpaper(path: Path, screens, fill: int, blur: bool):
    """Set wallpaper on specific Plasma screens (None = all), exactly as KDE's dialog would."""
    js = f"""
        var want = {json.dumps(screens)}, ds = desktops(), n = 0;
        for (var i = 0; i < ds.length; i++) {{
          var d = ds[i];
          if (want !== null && want.indexOf(d.screen) < 0) continue;
          d.wallpaperPlugin = "org.kde.image";
          d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
          d.writeConfig("Image", {json.dumps(path.resolve().as_uri())});
          d.writeConfig("FillMode", {int(fill)});
          d.writeConfig("Blur", {json.dumps(bool(blur))});
          n++;
        }}
        print(n);"""
    ok, out = plasma_script(js)
    if ok and out.strip().isdigit() and int(out) > 0:
        return True, f"set on {out.strip()} desktop(s)"
    return False, "no matching monitors" if out.strip() == "0" else (out or "Plasma did not respond")


KWRITECONFIG = next((k for k in ("kwriteconfig6", "kwriteconfig5") if shutil.which(k)), None)
KREADCONFIG = next((k for k in ("kreadconfig6", "kreadconfig5") if shutil.which(k)), None)
LOCK_GROUPS = ["Greeter", "Wallpaper", "org.kde.image", "General"]


def _kconfig_args(groups, key):
    args = ["--file", "kscreenlockerrc"]
    for g in groups:
        args += ["--group", g]
    return args + ["--key", key]


def lock_screen_image():
    """The KDE lock screen's current picture (file:// URI), or None if unknown."""
    if not KREADCONFIG:
        return None
    r = subprocess.run([KREADCONFIG, *_kconfig_args(LOCK_GROUPS, "Image")], capture_output=True, text=True)
    return r.stdout.strip() or None


def set_lock_screen(path: Path, fill: int, blur: bool):
    """Set the KDE lock screen picture (one picture on every monitor), as System Settings would."""
    if not KWRITECONFIG:
        return False, "kwriteconfig6 not found"
    uri = path.resolve().as_uri()
    writes = [
        (["Greeter"], "WallpaperPlugin", "org.kde.image"),
        (LOCK_GROUPS, "Image", uri),
        (LOCK_GROUPS, "PreviewImage", uri),
        (LOCK_GROUPS, "FillMode", str(int(fill))),
        (LOCK_GROUPS, "Blur", "true" if blur else "false"),
    ]
    for groups, key, value in writes:
        r = subprocess.run([KWRITECONFIG, *_kconfig_args(groups, key), value], capture_output=True, text=True)
        if r.returncode != 0:
            return False, (r.stdout + r.stderr).strip() or "could not write kscreenlockerrc"
    return True, "set on the lock screen"


def set_wallpaper(path: Path):
    """Set the desktop wallpaper on all screens (fallback when Plasma scripting isn't available)."""
    if shutil.which("plasma-apply-wallpaperimage"):
        cmd = ["plasma-apply-wallpaperimage", str(path)]
    elif shutil.which("gsettings"):
        uri = path.resolve().as_uri()
        subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri])
        cmd = ["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri]
    else:
        return False, "No supported wallpaper tool found"
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def make_handler(lib: Library, page_path: Path):
    """Request handler. Only the Wallfit page itself can use the API:

    * the Host header must be 127.0.0.1/localhost (blocks DNS-rebinding attacks);
    * loading the page sets a random per-run token in a SameSite=Strict cookie, and every
      other request must carry it (so other websites open in your browser can't use the API);
    * POSTs coming from another origin are refused.
    Local programs can authenticate with the X-Wallfit-Token header instead.
    """
    token = secrets.token_urlsafe(32)

    class H(BaseHTTPRequestHandler):
        TOKEN = token

        def log_message(self, *a):
            pass

        @property
        def _port(self):
            return self.server.server_address[1]

        @property
        def _cookie_name(self):
            return f"wallfit_{self._port}"

        def _guard(self, need_token=True):
            """Returns True if the request may proceed (otherwise a 403 has been sent)."""
            host = (self.headers.get("Host") or "").lower()
            if host not in (f"127.0.0.1:{self._port}", f"localhost:{self._port}"):
                self._send(403, b"forbidden", "text/plain")
                return False
            origin = self.headers.get("Origin")
            if self.command == "POST" and origin and origin.lower() != f"http://{host}":
                self._send(403, b"forbidden", "text/plain")
                return False
            if not need_token:
                return True
            got = self.headers.get("X-Wallfit-Token", "")
            if not got:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == self._cookie_name:
                        got = v
            if not hmac.compare_digest(got.encode(), token.encode()):
                self._send(403, b"forbidden", "text/plain")
                return False
            return True

        def _send(self, code, body, ctype="application/json", extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _file(self, p: Path):
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            with open(p, "rb") as f:
                data = f.read()
            self._send(200, data, ctype, {"Cache-Control": "max-age=86400"})

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                if not self._guard(need_token=False):
                    return
                cookie = f"{self._cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict"
                return self._send(200, page_path.read_bytes(), "text/html; charset=utf-8",
                                  {"Set-Cookie": cookie, "Cache-Control": "no-store"})
            if not self._guard():
                return
            if u.path == "/api/status":
                return self._send(200, lib.status())
            if u.path == "/api/images":
                with lib.lock:
                    return self._send(200, {"folder": str(lib.root) if lib.root else None, "images": lib.images})
            if u.path == "/api/pick-folder":
                if not lib.native_pick:
                    return self._send(404, {"path": None})
                return self._send(200, {"path": lib.native_pick(q.get("start", [""])[0])})
            if u.path == "/api/screens":
                s = kde_screens()
                return self._send(200, {"kde": s is not None, "screens": s or [], "fills": FILL_MODES,
                                        "lock": bool(KWRITECONFIG), "lockImage": lock_screen_image()})
            if u.path == "/api/browse":
                return self._send(200, browse(q.get("path", [""])[0]))
            if u.path.startswith("/thumb/"):
                p = CACHE_DIR / os.path.basename(u.path[len("/thumb/"):])
                return self._file(p) if p.is_file() else self._send(404, b"not found", "text/plain")
            if u.path == "/img":
                p = lib.path_for(q.get("p", [""])[0])
                return self._file(p) if p and p.is_file() else self._send(404, b"not found", "text/plain")
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            if not self._guard():
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return self._send(413, b"too large", "text/plain")
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, b"bad json", "text/plain")
            if u.path == "/api/raise":
                if lib.raise_window:
                    lib.raise_window()
                return self._send(200, {"ok": bool(lib.raise_window)})
            if u.path == "/api/open":
                root = Path(body.get("path", "")).expanduser()
                if not root.is_dir():
                    return self._send(400, {"ok": False, "msg": f"Not a folder: {root}"})
                lib.open(root.resolve(), bool(body.get("recursive", True)))
                return self._send(200, {"ok": True})
            p = lib.path_for(body.get("p", ""))
            if not p:
                return self._send(404, {"ok": False, "msg": "unknown image"})
            if u.path == "/api/wallpaper":
                target = p
                try:
                    if body.get("crop"):
                        target = make_crop(p, body["crop"])
                except Exception as e:
                    return self._send(500, {"ok": False, "msg": f"crop failed: {e}"})
                fill, blur = int(body.get("fill", 2)), bool(body.get("blur", False))
                screens, lock = body.get("screens"), bool(body.get("lock"))
                results = []
                if screens != []:  # [] = lock screen only
                    results.append(set_kde_wallpaper(target, screens, fill, blur) if QDBUS else set_wallpaper(target))
                if lock:
                    results.append(set_lock_screen(target, fill, blur))
                if not results:
                    return self._send(400, {"ok": False, "msg": "nothing selected"})
                ok = all(r[0] for r in results)
                msg = " · ".join(m for _, m in results)
                if ok and target != p:
                    msg += f" · cropped copy saved to {target}"
                return self._send(200 if ok else 500, {"ok": ok, "msg": msg})
            if u.path == "/api/reveal":
                subprocess.Popen(["xdg-open", str(p.parent)])
                return self._send(200, {"ok": True})
            self._send(404, b"not found", "text/plain")

    return H


def create_server(lib: Library, port: int, tries: int = 20):
    """Bind to 127.0.0.1:port, trying the next ports if it's taken."""
    handler = make_handler(lib, Path(__file__).with_name("index.html"))
    for p in range(port, port + tries):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), handler)
        except OSError:
            continue
    raise OSError(f"no free port in {port}-{port + tries - 1}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", help="folder to open (omit to pick one in the page)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-subfolders", action="store_true", help="don't include images in subfolders")
    a = ap.parse_args()

    lib = Library()
    if a.folder:
        root = Path(a.folder).expanduser().resolve()
        if not root.is_dir():
            sys.exit(f"Not a folder: {root}")
        lib.open(root, not a.no_subfolders)

    srv = create_server(lib, a.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Ready: {url}   (Ctrl+C to stop)", flush=True)
    if not a.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBye")


if __name__ == "__main__":
    main()
