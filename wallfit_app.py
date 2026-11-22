#!/usr/bin/env python3
"""Wallfit desktop app: the same interface, in its own KDE window.

Usage:  python3 wallfit_app.py [FOLDER]

The image server runs hidden inside the app (localhost only) and stops when the
window closes. With no FOLDER it reopens the last folder you used.
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QShortcut
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QFileDialog, QMainWindow

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wallfit as wp  # noqa: E402

APP_ID = "wallfit"
PORT = 48765  # fixed so the page's saved settings/shortlists persist between runs
ICON = "preferences-desktop-wallpaper"


def token_file(port):
    """Where the running app leaves its API token for a second launch to find (readable by you only)."""
    return Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / f"wallfit-{port}.token"


class FolderAsker(QObject):
    """Lets the server thread show KDE's folder dialog on the GUI thread and wait for the answer."""

    ask = pyqtSignal(str, object)

    def __init__(self, parent_window):
        super().__init__()
        self.win = parent_window
        self.ask.connect(self._ask, Qt.ConnectionType.QueuedConnection)

    def _ask(self, start, box):
        try:
            box["path"] = QFileDialog.getExistingDirectory(
                self.win, "Choose a folder of pictures", start or str(Path.home())) or None
        finally:
            box["done"].set()

    def __call__(self, start):
        box = {"done": threading.Event(), "path": None}
        self.ask.emit(start or "", box)
        box["done"].wait()
        return box["path"]


class Raiser(QObject):
    """Brings the window forward when a second launch hands over to this one."""

    sig = pyqtSignal()

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.sig.connect(self._raise, Qt.ConnectionType.QueuedConnection)

    def _raise(self):
        self.win.showNormal() if self.win.isMinimized() else None
        self.win.raise_()
        self.win.activateWindow()

    def __call__(self):
        self.sig.emit()


def hand_over_to_running_app():
    """If the app is already open, ask it to come forward (and open FOLDER if given). Returns True if so."""
    import json
    import urllib.request
    base = f"http://127.0.0.1:{PORT}"
    try:
        hdr = {"X-Wallfit-Token": token_file(PORT).read_text().strip(), "Content-Type": "application/json"}
        with urllib.request.urlopen(urllib.request.Request(base + "/api/status", headers=hdr), timeout=0.5) as r:
            if not json.load(r).get("native"):
                return False
        arg = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
        if arg and Path(arg).expanduser().is_dir():
            req = urllib.request.Request(base + "/api/open", method="POST", headers=hdr,
                                         data=json.dumps({"path": str(Path(arg).expanduser().resolve())}).encode())
            urllib.request.urlopen(req, timeout=2).close()
        urllib.request.urlopen(urllib.request.Request(base + "/api/raise", method="POST", headers=hdr, data=b"{}"), timeout=2).close()
        return True
    except Exception:
        return False


class Page(QWebEnginePage):
    """Keep the app on its own page; open any outside link in the normal browser."""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() in ("http", "https") and url.host() != "127.0.0.1":
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class Window(QMainWindow):
    def __init__(self, url, profile, settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Wallfit")
        self.setWindowIcon(QIcon.fromTheme(ICON))
        self.view = QWebEngineView(self)
        page = Page(profile, self.view)
        page.setBackgroundColor(QColor("#16181c"))  # no white flash while loading
        page.titleChanged.connect(lambda t: self.setWindowTitle(t or "Wallfit"))
        self.view.setPage(page)
        self.setCentralWidget(self.view)
        self.view.load(QUrl(url))

        QShortcut(QKeySequence("F11"), self, self.toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+R"), self, self.view.reload)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)

        geo = settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1500, 950)

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)


def main():
    if hand_over_to_running_app():
        return
    QApplication.setApplicationName(APP_ID)
    QApplication.setApplicationDisplayName("Wallfit")
    QApplication.setDesktopFileName(APP_ID)  # ties the window to its start-menu entry/icon on Wayland
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon.fromTheme(ICON))
    settings = QSettings(APP_ID, APP_ID)

    # which folder to open: command line, else the last one used
    lib = wp.Library()
    arg = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    folder = Path(arg).expanduser() if arg else (Path(settings.value("folder")) if settings.value("folder") else None)
    recursive = settings.value("recursive", True, type=bool)
    if folder and folder.is_dir():
        lib.open(folder.resolve(), recursive)

    srv = wp.create_server(lib, PORT)
    port = srv.server_address[1]
    tf = token_file(port)
    tf.unlink(missing_ok=True)  # recreate so the 0600 permissions always apply
    fd = os.open(tf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(srv.RequestHandlerClass.TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    profile = QWebEngineProfile(APP_ID, app)  # named profile = settings and shortlists saved on disk
    win = Window(f"http://127.0.0.1:{port}/", profile, settings)
    lib.native_pick = FolderAsker(win)
    lib.raise_window = Raiser(win)
    win.show()

    code = app.exec()
    st = lib.status()
    if st["folder"]:
        settings.setValue("folder", st["folder"])
        settings.setValue("recursive", st["recursive"])
    srv.shutdown()
    tf.unlink(missing_ok=True)
    sys.exit(code)


if __name__ == "__main__":
    main()
