#!/usr/bin/env python3
"""
Colab Media Studio — Zero-dependency Local Mac Backend
Connects your local browser UI to a remote Google Colab GPU runtime,
automatically saves generated images/videos locally, tracks compute unit usage,
and supports one-click remote runtime shutdown.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
OUTPUTS_DIR = ROOT_DIR / "outputs"
COLAB_DIR = ROOT_DIR / "colab"
CONFIG_PATH = ROOT_DIR / "config.json"
HISTORY_PATH = OUTPUTS_DIR / "history.json"

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

_STATE_LOCK = threading.Lock()
_PENDING_JOBS: dict[str, dict[str, Any]] = {}
_DOWNLOADED_JOBS: dict[str, dict[str, Any]] = {}


def load_config() -> dict[str, Any]:
    default = {
        "colab_url": "",
        "colab_token": "",
        "comfyui_url": "",
        "idle_alert_minutes": 10,
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                default.update(data)
        except Exception:
            pass
    return default


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    for key in ("colab_url", "colab_token", "comfyui_url", "idle_alert_minutes"):
        if key in cfg:
            val = cfg[key]
            if isinstance(val, str):
                val = val.strip().rstrip("/")
            current[key] = val
    CONFIG_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if (OUTPUTS_DIR / item.get("filename", "")).exists()]
    except Exception:
        pass
    return []


def save_history(items: list[dict[str, Any]]) -> None:
    HISTORY_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def colab_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
    expect_bytes: bool = False,
) -> tuple[int, Any]:
    cfg = load_config()
    base_url = cfg.get("colab_url", "").strip().rstrip("/")
    token = cfg.get("colab_token", "").strip()
    if not base_url:
        return 400, {"error": "尚未配置 Colab 连接地址，请先在顶部粘贴连接码。"}

    url = f"{base_url}{path}"
    headers = {
        "User-Agent": "ColabMediaStudio/1.0",
        "Accept": "*/*" if expect_bytes else "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if expect_bytes:
                return resp.status, raw
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return resp.status, {"raw": raw.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(body)
        except Exception:
            err_json = {"error": f"Colab 返回错误 ({e.code}): {body[:300]}"}
        return e.code, err_json
    except urllib.error.URLError as e:
        return 502, {"error": f"无法连接到 Colab（可能已休眠或链接失效）: {e.reason}"}
    except TimeoutError:
        return 504, {"error": "连接 Colab 超时，请检查 Colab 页面是否仍在运行。"}
    except Exception as e:
        return 500, {"error": f"请求失败: {e}"}


def persist_completed_job(job_id: str, job_data: dict[str, Any]) -> dict[str, Any]:
    with _STATE_LOCK:
        if job_id in _DOWNLOADED_JOBS:
            return _DOWNLOADED_JOBS[job_id]

    status, raw_bytes = colab_request("GET", f"/api/jobs/{job_id}/result", timeout=180.0, expect_bytes=True)
    if status != 200 or not isinstance(raw_bytes, (bytes, bytearray)):
        raise RuntimeError(f"下载云端生成结果失败 (HTTP {status})")

    meta = _PENDING_JOBS.get(job_id, {})
    ext = (job_data.get("file_ext") or ("mp4" if meta.get("task") == "video" else "png")).lstrip(".")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(meta.get("model_id", "media")))
    filename = f"{ts}_{safe_model}_{job_id[:8]}.{ext}"
    file_path = OUTPUTS_DIR / filename
    file_path.write_bytes(raw_bytes)

    item = {
        "id": job_id,
        "filename": filename,
        "local_url": f"/outputs/{urllib.parse.quote(filename)}",
        "file_path": str(file_path),
        "task": meta.get("task", "video" if ext == "mp4" else "image"),
        "model_id": meta.get("model_id", ""),
        "prompt": meta.get("prompt", ""),
        "negative_prompt": meta.get("negative_prompt", ""),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "num_frames": meta.get("num_frames"),
        "fps": meta.get("fps"),
        "steps": meta.get("steps"),
        "seed": job_data.get("seed", meta.get("seed")),
        "gpu_name": job_data.get("gpu_name", ""),
        "elapsed_seconds": job_data.get("elapsed_seconds"),
        "drive_path": job_data.get("drive_path"),
        "size_bytes": len(raw_bytes),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    with _STATE_LOCK:
        history = load_history()
        history.insert(0, item)
        save_history(history)
        _DOWNLOADED_JOBS[job_id] = item
    return item


class StudioRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep terminal clean while still logging important errors
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)

    def send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def serve_file(self, path: Path, support_range: bool = False) -> None:
        if not path.exists() or not path.is_file():
            self.send_json(404, {"error": "File not found"})
            return

        ctype, _ = mimetypes.guess_type(str(path))
        if not ctype:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"

        file_size = path.stat().st_size
        range_header = self.headers.get("Range") if support_range else None

        if range_header and range_header.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if m:
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                if start <= end < file_size:
                    length = end - start + 1
                    self.send_response(HTTPStatus.PARTIAL_CONTENT)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    self.send_header("Content-Length", str(length))
                    self.end_headers()
                    with path.open("rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        if not support_range:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self.serve_file(STATIC_DIR / "index.html")
        if path in ("/styles.css", "/app.js"):
            return self.serve_file(STATIC_DIR / path.lstrip("/"))

        if path.startswith("/outputs/"):
            filename = urllib.parse.unquote(path[len("/outputs/"):])
            if "/" in filename or "\\" in filename or ".." in filename:
                return self.send_json(400, {"error": "Invalid filename"})
            return self.serve_file(OUTPUTS_DIR / filename, support_range=True)

        if path == "/local/config":
            cfg = load_config()
            return self.send_json(
                200,
                {
                    **cfg,
                    "outputs_dir": str(OUTPUTS_DIR),
                    "colab_dir": str(COLAB_DIR),
                },
            )

        if path == "/local/status":
            cfg = load_config()
            if not cfg.get("colab_url"):
                return self.send_json(200, {"connected": False, "configured": False, "health": None, "config": cfg})
            status, data = colab_request("GET", "/api/health", timeout=8.0)
            if status == 200 and isinstance(data, dict) and data.get("ok"):
                return self.send_json(200, {"connected": True, "configured": True, "health": data, "config": cfg})
            return self.send_json(
                200,
                {
                    "connected": False,
                    "configured": True,
                    "error": data.get("error", f"HTTP {status}") if isinstance(data, dict) else str(data),
                    "health": None,
                    "config": cfg,
                },
            )

        if path == "/local/gallery":
            return self.send_json(200, {"items": load_history(), "outputs_dir": str(OUTPUTS_DIR)})

        if path.startswith("/local/jobs/"):
            job_id = path[len("/local/jobs/"):].strip("/")
            status, data = colab_request("GET", f"/api/jobs/{job_id}", timeout=15.0)
            if status != 200:
                return self.send_json(status, data)
            if isinstance(data, dict) and data.get("status") == "completed":
                try:
                    item = persist_completed_job(job_id, data)
                    data["local_item"] = item
                except Exception as e:
                    data["download_error"] = str(e)
            return self.send_json(200, data)

        self.send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/local/config":
            body = self.read_json_body()
            # Support single-paste connect code format: URL::TOKEN or JSON
            connect_code = (body.get("connect_code") or "").strip()
            if connect_code:
                if "::" in connect_code:
                    url_part, tok_part = connect_code.split("::", 1)
                    body["colab_url"] = url_part.strip()
                    body["colab_token"] = tok_part.strip()
                elif connect_code.startswith("{"):
                    try:
                        parsed_code = json.loads(connect_code)
                        body["colab_url"] = parsed_code.get("url", "")
                        body["colab_token"] = parsed_code.get("token", "")
                    except Exception:
                        pass
            saved = save_config(body)
            return self.send_json(200, {"ok": True, "config": saved})

        if path == "/local/generate":
            body = self.read_json_body()
            # Allow 1-click Image-to-Video directly from local gallery item
            source_id = body.pop("source_gallery_id", None)
            if source_id and not body.get("init_image_base64"):
                for item in load_history():
                    if item.get("id") == source_id:
                        img_path = OUTPUTS_DIR / item["filename"]
                        if img_path.exists():
                            b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
                            body["init_image_base64"] = b64
                        break

            status, data = colab_request("POST", "/api/generate", payload=body, timeout=30.0)
            if status == 200 and isinstance(data, dict) and data.get("job_id"):
                meta = {k: v for k, v in body.items() if k != "init_image_base64"}
                _PENDING_JOBS[data["job_id"]] = meta
            return self.send_json(status, data)

        if path == "/local/unload":
            status, data = colab_request("POST", "/api/unload", payload={}, timeout=20.0)
            return self.send_json(status, data)

        if path == "/local/shutdown":
            status, data = colab_request("POST", "/api/shutdown", payload={}, timeout=15.0)
            return self.send_json(status, data)

        if path == "/local/open-folder":
            body = self.read_json_body()
            target_name = body.get("target", "outputs")
            target_dir = COLAB_DIR if target_name == "colab" else OUTPUTS_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(target_dir)])
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(target_dir)])
                elif sys.platform == "win32":
                    os.startfile(str(target_dir))  # type: ignore[attr-defined]
                return self.send_json(200, {"ok": True, "path": str(target_dir)})
            except Exception as e:
                return self.send_json(500, {"error": str(e)})

        self.send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/local/gallery/"):
            item_id = path[len("/local/gallery/"):].strip("/")
            with _STATE_LOCK:
                history = load_history()
                remaining = []
                deleted = None
                for item in history:
                    if item.get("id") == item_id:
                        deleted = item
                        f = OUTPUTS_DIR / item.get("filename", "")
                        if f.exists():
                            f.unlink(missing_ok=True)
                    else:
                        remaining.append(item)
                save_history(remaining)
            if deleted:
                return self.send_json(200, {"ok": True, "deleted": item_id})
            return self.send_json(404, {"error": "未找到该作品"})
        self.send_json(404, {"error": "Not found"})


def pick_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main() -> None:
    port = int(os.environ.get("STUDIO_PORT", "0")) or pick_port(8765)
    no_browser = "--no-browser" in sys.argv
    server = ThreadingHTTPServer(("127.0.0.1", port), StudioRequestHandler)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}"
    print("=" * 56)
    print("  Colab Media Studio 本地桌面应用已启动")
    print(f"  访问地址: {url}")
    print(f"  本地作品目录: {OUTPUTS_DIR}")
    print("  按 Ctrl+C 可关闭本地服务（关闭前别忘了释放 Colab 算力）")
    print("=" * 56)

    if not no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已关闭 Colab Media Studio 本地服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
