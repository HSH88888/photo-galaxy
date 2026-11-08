"""Photo Galaxy — Windows Desktop Application entry point.

Opens Photo Galaxy in a dedicated native Windows window (WebView2 via pywebview)
and runs the local backend server in a background thread. When the window is
closed, the process exits cleanly.
"""
import sys
import threading

import webview

import server


def main():
    force_scan = "--rescan" in sys.argv
    threading.Thread(target=server.load, args=(force_scan,), daemon=True).start()
    srv, url = server.start_background_server()

    window = webview.create_window(
        title="Photo Galaxy",
        url=url,
        width=1480,
        height=920,
        min_size=(800, 600),
        background_color="#0d0d17",
        text_select=False,
    )
    try:
        webview.start()
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
