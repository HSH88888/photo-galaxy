"""Photo Galaxy local server (Python stdlib + Pillow only).

    python server.py            # uses config.json
    python server.py --rescan   # force a fresh folder scan
"""
import gzip
import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import imaging
import scanner

for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_FROZEN = getattr(sys, "frozen", False)
BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(sys.executable) if IS_FROZEN else os.path.dirname(os.path.abspath(__file__))
_LOCAL_STATIC = os.path.join(APP_DIR, "static")
STATIC = _LOCAL_STATIC if os.path.isdir(_LOCAL_STATIC) else os.path.join(BUNDLE_DIR, "static")
DB_PATH = os.path.join(APP_DIR, "photos.db")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


def _load_config():
    for p in (CONFIG_PATH, os.path.join(BUNDLE_DIR, "config.json")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return {
        "root": "H:\\D드라이브 사진원본저장소",
        "extensions": [".jpg", ".jpeg", ".png", ".webp"],
        "exclude_names": ["node_modules", ".git", "venv", ".venv", "__pycache__", "$recycle.bin", "system volume information"],
        "port": 8765,
    }


CONFIG = _load_config()
STATE = {"tree_gz": None, "paths": [], "scanning": False, "mode": CONFIG.get("mode", "folder")}
LOCK = threading.Lock()


def save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def pick_folder_dialog(initial_dir=""):
    """Open a native Windows folder selection dialog and return the chosen path or None."""
    try:
        import webview
        if webview.windows:
            dlg_type = getattr(webview, "FOLDER_DIALOG", None)
            if dlg_type is None and hasattr(webview, "FileDialog"):
                dlg_type = webview.FileDialog.FOLDER
            res = webview.windows[0].create_file_dialog(
                dlg_type,
                directory=initial_dir if os.path.isdir(initial_dir) else "",
            )
            if res:
                return res[0] if isinstance(res, (list, tuple)) else str(res)
            return None
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(
            initialdir=initial_dir if os.path.isdir(initial_dir) else "",
            title="사진 폴더 선택 - Photo Galaxy",
        )
        root.destroy()
        return os.path.normpath(chosen) if chosen else None
    except Exception:
        return None


def load(force_scan=False):
    with LOCK:
        STATE["scanning"] = True
    try:
        if force_scan or not scanner.has_data(DB_PATH, CONFIG["root"]):
            print(f"스캔 중: {CONFIG['root']}")
            scanner.scan(CONFIG["root"], CONFIG["extensions"], CONFIG["exclude_names"], DB_PATH)
        tree, paths = scanner.build_tree(DB_PATH, STATE.get("mode", "folder"))
        body = gzip.compress(json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
        with LOCK:
            STATE["tree_gz"], STATE["paths"] = body, paths
        print(f"트리 준비 완료: 폴더 {len(tree['folders'])}개, 사진 {len(paths)}장")
    finally:
        with LOCK:
            STATE["scanning"] = False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC, **kw)

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, body, ctype, extra=None, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _photo_path(self, q):
        try:
            i = int(q.get("id", ["-1"])[0])
            return STATE["paths"][i]
        except (ValueError, IndexError):
            return None

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/prefs":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    prefs = CONFIG.setdefault("prefs", {})
                    prefs.update(data)
                    save_config()
            except Exception:
                pass
            return self._send(b'{"ok":true}', "application/json; charset=utf-8")
        if url.path == "/api/pick-folder":
            chosen = pick_folder_dialog(CONFIG.get("root", ""))
            if chosen and os.path.isdir(chosen):
                CONFIG["root"] = os.path.normpath(chosen)
                save_config()
                with LOCK:
                    STATE["tree_gz"] = None
                    STATE["scanning"] = True
                threading.Thread(target=load, args=(False,), daemon=True).start()
                body = json.dumps({"changed": True, "root": CONFIG["root"]}, ensure_ascii=False).encode("utf-8")
                return self._send(body, "application/json; charset=utf-8")
            body = json.dumps({"changed": False, "root": CONFIG.get("root", "")}, ensure_ascii=False).encode("utf-8")
            return self._send(body, "application/json; charset=utf-8")
        return self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            if url.path == "/api/tree":
                if STATE["tree_gz"] is None or STATE["scanning"]:
                    return self._send(b'{"scanning":true}', "application/json", status=HTTPStatus.SERVICE_UNAVAILABLE)
                return self._send(STATE["tree_gz"], "application/json; charset=utf-8",
                                  {"Content-Encoding": "gzip", "Cache-Control": "no-cache"})
            if url.path == "/api/status":
                return self._send(json.dumps({"scanning": STATE["scanning"]}).encode(), "application/json")
            if url.path == "/api/prefs":
                body = json.dumps(CONFIG.get("prefs", {}), ensure_ascii=False).encode("utf-8")
                return self._send(body, "application/json; charset=utf-8", {"Cache-Control": "no-cache"})
            if url.path == "/api/rescan":
                if not STATE["scanning"]:
                    with LOCK:
                        STATE["scanning"] = True
                    threading.Thread(target=load, args=(True,), daemon=True).start()
                return self._send(b'{"ok":true}', "application/json")
            if url.path == "/api/set-mode":
                mode = q.get("mode", ["folder"])[0]
                if mode in ("folder", "month", "day") and not STATE["scanning"]:
                    with LOCK:
                        STATE["mode"] = mode
                        STATE["tree_gz"] = None
                        STATE["scanning"] = True
                    CONFIG["mode"] = mode
                    save_config()
                    threading.Thread(target=load, args=(False,), daemon=True).start()
                return self._send(json.dumps({"ok": True, "mode": STATE["mode"]}).encode(), "application/json")
            if url.path == "/api/open-file":
                path = self._photo_path(q)
                if not path or not os.path.isfile(path):
                    return self.send_error(HTTPStatus.NOT_FOUND)
                os.startfile(path)
                return self._send(b'{"ok":true}', "application/json")
            if url.path in ("/api/thumb", "/api/img", "/api/orig"):
                path = self._photo_path(q)
                if not path or not os.path.isfile(path):
                    return self.send_error(HTTPStatus.NOT_FOUND)
                cache = {"Cache-Control": "private, max-age=86400"}
                if url.path == "/api/thumb":
                    return self._send(imaging.thumbnail(path), "image/jpeg", cache)
                if url.path == "/api/img":
                    size = max(64, min(4096, int(q.get("s", ["1024"])[0])))
                    return self._send(imaging.resized(path, size), "image/jpeg", cache)
                with open(path, "rb") as fh:
                    body = fh.read()
                return self._send(body, mimetypes.guess_type(path)[0] or "application/octet-stream", cache)
        except (ConnectionError, BrokenPipeError):
            return
        except Exception as e:  # decoding errors etc.
            try:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e)[:200])
            except Exception:
                pass
            return
        return super().do_GET()


def start_background_server(preferred_port=None):
    """Start the HTTP server in a daemon thread and return (server, url)."""
    port = preferred_port or CONFIG.get("port", 8765)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}/"


def main():
    force = "--rescan" in sys.argv
    load(force)
    srv, url = start_background_server()
    print(f"Photo Galaxy 실행 중: {url}  (종료: Ctrl+C)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
