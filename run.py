"""Start the local app and open a browser once HTTP is ready."""
import argparse
import os
import threading
import time
import urllib.request
import webbrowser

import uvicorn


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("MINERU_MODEL_SOURCE", "modelscope")
    url = f"http://127.0.0.1:{args.port}"

    def open_when_ready():
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open(url + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except OSError:
                time.sleep(0.25)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
