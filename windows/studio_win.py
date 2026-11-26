#!/usr/bin/env python3
"""
Colab Canvas Studio — Standalone Windows Desktop App Entrypoint.

Features:
- Bundles `google-colab-cli` inside the same executable (invoked via `--colab-cli`),
  so Windows users need ZERO Python, uv, or CLI installation!
- Launches local infinite-canvas backend on localhost and opens a native dark
  Windows desktop window powered by Microsoft Edge WebView2 (`pywebview`),
  falling back cleanly to Edge/Chrome `--app` mode if WebView2 is unavailable.
- Supports `--headless-test` for automated Windows CI verification.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _ensure_valid_stdio() -> None:
    """PyInstaller `--windowed` sets stdio to None when launched without redirected pipes."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")


def _run_bundled_colab_cli() -> None:
    """Delegate `--colab-cli ...` directly to the bundled google-colab-cli."""
    _ensure_valid_stdio()
    sys.argv = ["colab"] + sys.argv[2:]
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    from colab_cli.cli import main as colab_main
    colab_main()


def _configure_runtime_env() -> None:
    # Point ColabManager to our bundled CLI sub-invocation (no external colab.exe required)
    if getattr(sys, "frozen", False):
        cli_cmd = [sys.executable, "--colab-cli"]
    else:
        cli_cmd = [sys.executable, str(Path(__file__).resolve()), "--colab-cli"]
    os.environ["COLAB_CLI_CMD_JSON"] = json.dumps(cli_cmd)

    # Persistent user-friendly data folder in Documents\ColabCanvasStudio
    if not os.environ.get("STUDIO_DATA_DIR"):
        data_dir = Path.home() / "Documents" / "ColabCanvasStudio"
        data_dir.mkdir(parents=True, exist_ok=True)
        os.environ["STUDIO_DATA_DIR"] = str(data_dir)


def _import_server_module():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        local_app_dir = Path(sys._MEIPASS) / "local_app"
    else:
        local_app_dir = Path(__file__).resolve().parent.parent / "local_app"
    if str(local_app_dir) not in sys.path:
        sys.path.insert(0, str(local_app_dir))
    import server as studio_server  # type: ignore
    return studio_server


def _start_local_server():
    studio_server = _import_server_module()
    port = int(os.environ.get("STUDIO_PORT", "0")) or studio_server.pick_port(8765)
    httpd = studio_server.ThreadingHTTPServer(("127.0.0.1", port), studio_server.StudioHandler)
    actual_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, actual_port


def _run_headless_smoke_test() -> int:
    studio_server = _import_server_module()
    port = int(os.environ.get("STUDIO_PORT", "0")) or studio_server.pick_port(8765)
    httpd = studio_server.ThreadingHTTPServer(("127.0.0.1", port), studio_server.StudioHandler)
    actual_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{actual_port}"
    try:
        # 1. HTML + static assets (Connection: close avoids HTTP/1.1 keep-alive hang)
        for path, must_contain in (("/", b"Colab Canvas Studio"), ("/app.js", b"boot")):
            req = urllib.request.Request(f"{base}{path}", headers={"Connection": "close"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
                assert resp.status == 200 and must_contain in body

        # 2. Status check confirms bundled colab CLI is detected
        req = urllib.request.Request(f"{base}/api/status?force=1", headers={"Connection": "close"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            snap = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200 and snap.get("cli_installed") is True, f"Status failed: {snap}"

        # 3. Real OAuth URL generation confirms bundled oauth_config.json works on Windows
        req = urllib.request.Request(
            f"{base}/api/auth/start",
            data=b"{}",
            headers={"Content-Type": "application/json", "Connection": "close"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            auth_res = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200 and (
                auth_res.get("already_authenticated")
                or str(auth_res.get("auth_url", "")).startswith("https://accounts.google.com/")
            ), f"OAuth start failed: {auth_res}"

        print("WINDOWS_HEADLESS_SMOKE_TEST_PASSED", flush=True)
        return 0
    finally:
        try:
            studio_server.COLAB._cleanup_oauth_proc()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass


def _fallback_browser_app(url: str, httpd) -> None:
    """Fallback to Microsoft Edge / Chrome app window if WebView2 isn't available."""
    browsers = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    launched_proc = None
    for b in browsers:
        if os.path.exists(b):
            launched_proc = subprocess.Popen([b, f"--app={url}"])
            break
    if launched_proc is not None:
        launched_proc.wait()
    else:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    httpd.shutdown()


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--colab-cli":
        _run_bundled_colab_cli()
        return

    _ensure_valid_stdio()
    _configure_runtime_env()

    if "--headless-test" in sys.argv:
        code = _run_headless_smoke_test()
        os._exit(code)

    httpd, port = _start_local_server()
    url = f"http://127.0.0.1:{port}/"

    try:
        import webview  # type: ignore

        webview.create_window(
            "Colab Canvas Studio",
            url,
            width=1440,
            height=900,
            min_size=(960, 640),
            background_color="#08090e",
            text_select=True,
        )
        webview.start(gui="edgechromium")
    except Exception:
        _fallback_browser_app(url, httpd)
    finally:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
