#!/usr/bin/env python3
"""
Colab Media Studio — Infinite Canvas Local Mac App Server
Directly drives your Google Colab GPU via official `google-colab-cli` with ZERO
need to open Google Colab in a browser.
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
import urllib.parse
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Ensure local_app package imports cleanly regardless of CWD
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from colab_manager import ColabManager  # noqa: E402

ROOT_DIR = THIS_DIR.parent
STATIC_DIR = THIS_DIR / "static"
OUTPUTS_DIR = ROOT_DIR / "outputs"
CONFIG_PATH = ROOT_DIR / "config.json"
HISTORY_PATH = OUTPUTS_DIR / "history.json"

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

_STATE_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def load_settings() -> dict[str, Any]:
    default = {
        "preferred_gpu": "L4",
        "auto_stop_minutes": 10,
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                default.update(data)
        except Exception:
            pass
    return default


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    cur = load_settings()
    for key in ("preferred_gpu", "auto_stop_minutes"):
        if key in patch:
            cur[key] = patch[key]
    CONFIG_PATH.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
    return cur


def load_items() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [it for it in data if (OUTPUTS_DIR / it.get("filename", "")).exists()]
    except Exception:
        pass
    return []


def save_items(items: list[dict[str, Any]]) -> None:
    HISTORY_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def next_canvas_position(items: list[dict[str, Any]], parent_id: str | None = None) -> tuple[int, int]:
    if parent_id:
        for it in items:
            if it.get("id") == parent_id:
                px = int(it.get("x", 120))
                py = int(it.get("y", 120))
                # Offset neatly to the right of parent node so SVG connector looks clean
                siblings = sum(1 for x in items if x.get("parent_id") == parent_id)
                return px + 440, py + siblings * 320
    idx = len(items)
    col = idx % 3
    row = idx // 3
    return 120 + col * 400, 120 + row * 340


COLAB = ColabManager(get_settings=load_settings)


def _run_job_thread(
    job_id: str,
    params: dict[str, Any],
    init_image_local: Path | None,
    parent_id: str | None,
    target_x: int,
    target_y: int,
) -> None:
    t0 = time.time()

    def on_update(patch: dict[str, Any]) -> None:
        with _STATE_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id].update(patch)
                _JOBS[job_id]["elapsed_seconds"] = round(time.time() - t0, 1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(params.get("model_id", "media")))
    out_stub = OUTPUTS_DIR / f"{ts}_{safe_model}_{job_id[:8]}"

    try:
        meta = COLAB.run_generation(
            job_id=job_id,
            params=params,
            init_image_local=init_image_local,
            output_local_path=out_stub,
            on_update=on_update,
        )

        filename = meta["filename"]
        file_path = OUTPUTS_DIR / filename
        ext = meta.get("file_ext", "png")
        item = {
            "id": job_id,
            "kind": "video" if ext == "mp4" else "image",
            "filename": filename,
            "local_url": f"/outputs/{urllib.parse.quote(filename)}",
            "model_id": params.get("model_id", ""),
            "prompt": params.get("prompt", ""),
            "negative_prompt": params.get("negative_prompt", ""),
            "width": meta.get("width", params.get("width")),
            "height": meta.get("height", params.get("height")),
            "num_frames": params.get("num_frames"),
            "steps": params.get("steps"),
            "seed": meta.get("seed"),
            "gpu_name": meta.get("gpu_name", ""),
            "elapsed_seconds": meta.get("elapsed_seconds", round(time.time() - t0, 1)),
            "parent_id": parent_id,
            "x": target_x,
            "y": target_y,
            "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

        with _STATE_LOCK:
            items = load_items()
            items.insert(0, item)
            save_items(items)
            _JOBS[job_id].update(
                {
                    "status": "completed",
                    "stage": "已完成！",
                    "progress": 100,
                    "elapsed_seconds": round(time.time() - t0, 1),
                    "item": item,
                }
            )
    except Exception as e:
        with _STATE_LOCK:
            _JOBS[job_id].update(
                {
                    "status": "failed",
                    "stage": "生成失败",
                    "error": str(e),
                    "elapsed_seconds": round(time.time() - t0, 1),
                }
            )


class StudioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
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

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def serve_file(self, path: Path, support_range: bool = False) -> None:
        if not path.exists() or not path.is_file():
            return self.send_json(404, {"error": "Not found"})
        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"

        file_size = path.stat().st_size
        range_hdr = self.headers.get("Range") if support_range else None
        if range_hdr and range_hdr.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", range_hdr)
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
                        rem = length
                        while rem > 0:
                            chunk = f.read(min(65536, rem))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            rem -= len(chunk)
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
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            return self.serve_file(STATIC_DIR / "index.html")
        if path in ("/styles.css", "/app.js"):
            return self.serve_file(STATIC_DIR / path.lstrip("/"))
        if path.startswith("/outputs/"):
            fname = urllib.parse.unquote(path[len("/outputs/"):])
            if "/" in fname or "\\" in fname or ".." in fname:
                return self.send_json(400, {"error": "Invalid filename"})
            return self.serve_file(OUTPUTS_DIR / fname, support_range=True)

        if path == "/api/status":
            force = qs.get("force", ["0"])[0] == "1"
            snap = COLAB.refresh_state(force=force)
            with _STATE_LOCK:
                active_jobs = [j for j in _JOBS.values() if j.get("status") not in ("completed", "failed")]
            snap["active_jobs"] = active_jobs
            snap["outputs_dir"] = str(OUTPUTS_DIR)
            return self.send_json(200, snap)

        if path == "/api/canvas":
            with _STATE_LOCK:
                items = load_items()
                active_jobs = [j for j in _JOBS.values() if j.get("status") not in ("completed", "failed")]
            return self.send_json(200, {"items": items, "active_jobs": active_jobs, "outputs_dir": str(OUTPUTS_DIR)})

        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/"):].strip("/")
            with _STATE_LOCK:
                job = _JOBS.get(job_id)
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            return self.send_json(200, job)

        self.send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/auth/start":
            try:
                res = COLAB.start_oauth_login()
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(500, {"error": str(e)})

        if path == "/api/auth/complete":
            try:
                body = self.read_json()
                res = COLAB.complete_oauth_login(body.get("code", ""))
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        if path == "/api/settings":
            body = self.read_json()
            saved = save_settings(body)
            return self.send_json(200, {"ok": True, "settings": saved})

        if path == "/api/gpu/start":
            try:
                body = self.read_json()
                gpu = body.get("gpu") or load_settings().get("preferred_gpu", "L4")
                save_settings({"preferred_gpu": gpu})
                snap = COLAB.start_gpu(gpu=gpu)
                return self.send_json(200, snap)
            except Exception as e:
                return self.send_json(500, {"error": str(e)})

        if path == "/api/gpu/stop":
            try:
                snap = COLAB.stop_gpu()
                return self.send_json(200, snap)
            except Exception as e:
                return self.send_json(500, {"error": str(e)})

        if path == "/api/materials/upload":
            try:
                body = self.read_json()
                b64 = body.get("data_base64", "")
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                raw = base64.b64decode(b64)
                orig_name = body.get("filename", "material.png")
                ext = Path(orig_name).suffix.lower()
                if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                    ext = ".png"
                item_id = uuid.uuid4().hex
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{ts}_material_{item_id[:8]}{ext}"
                (OUTPUTS_DIR / filename).write_bytes(raw)

                with _STATE_LOCK:
                    items = load_items()
                    x = body.get("x")
                    y = body.get("y")
                    if x is None or y is None:
                        x, y = next_canvas_position(items)
                    item = {
                        "id": item_id,
                        "kind": "material",
                        "filename": filename,
                        "local_url": f"/outputs/{urllib.parse.quote(filename)}",
                        "model_id": "本地素材",
                        "prompt": body.get("label") or orig_name,
                        "x": int(x),
                        "y": int(y),
                        "size_bytes": len(raw),
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    items.insert(0, item)
                    save_items(items)
                return self.send_json(200, {"ok": True, "item": item})
            except Exception as e:
                return self.send_json(400, {"error": f"导入素材失败: {e}"})

        if path == "/api/generate":
            body = self.read_json()
            if not COLAB.authenticated:
                COLAB.refresh_state(force=True)
                if not COLAB.authenticated:
                    return self.send_json(401, {"error": "请先点击右上角「登录 Google 账号」完成授权。"})

            with _STATE_LOCK:
                running = [j for j in _JOBS.values() if j.get("status") not in ("completed", "failed")]
                if running:
                    return self.send_json(409, {"error": "当前已有任务正在生成，请等待当前任务完成。"})

            parent_id = body.get("parent_id")
            init_image_local: Path | None = None
            if parent_id:
                for it in load_items():
                    if it.get("id") == parent_id:
                        cand = OUTPUTS_DIR / it["filename"]
                        if cand.exists():
                            init_image_local = cand
                        break

            with _STATE_LOCK:
                items = load_items()
                tx = body.get("x")
                ty = body.get("y")
                if tx is None or ty is None:
                    tx, ty = next_canvas_position(items, parent_id=parent_id)
                job_id = uuid.uuid4().hex
                job_obj = {
                    "job_id": job_id,
                    "task": body.get("task", "image"),
                    "model_id": body.get("model_id", "flux-schnell"),
                    "prompt": body.get("prompt", ""),
                    "parent_id": parent_id,
                    "x": int(tx),
                    "y": int(ty),
                    "status": "queued",
                    "stage": "已提交，正在准备云端 GPU...",
                    "progress": 2,
                    "elapsed_seconds": 0.0,
                    "error": None,
                    "item": None,
                }
                _JOBS[job_id] = job_obj

            threading.Thread(
                target=_run_job_thread,
                args=(job_id, body, init_image_local, parent_id, int(tx), int(ty)),
                daemon=True,
            ).start()
            return self.send_json(200, job_obj)

        if path == "/api/canvas/layout":
            with _STATE_LOCK:
                items = load_items()
                # Group nicely: roots in rows, children right next to parents
                by_id = {it["id"]: it for it in items}
                roots = [it for it in reversed(items) if not it.get("parent_id") or it.get("parent_id") not in by_id]
                children_map: dict[str, list[dict[str, Any]]] = {}
                for it in reversed(items):
                    pid = it.get("parent_id")
                    if pid and pid in by_id:
                        children_map.setdefault(pid, []).append(it)

                cur_y = 110
                for root in roots:
                    root["x"] = 120
                    root["y"] = cur_y
                    kids = children_map.get(root["id"], [])
                    for idx, kid in enumerate(kids):
                        kid["x"] = 560 + (idx // 2) * 420
                        kid["y"] = cur_y + (idx % 2) * 320
                    rows_used = max(1, min(2, len(kids)))
                    cur_y += rows_used * 350
                save_items(items)
            return self.send_json(200, {"ok": True, "items": items})

        if path == "/api/open-outputs":
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(OUTPUTS_DIR)])
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(OUTPUTS_DIR)])
                return self.send_json(200, {"ok": True, "path": str(OUTPUTS_DIR)})
            except Exception as e:
                return self.send_json(500, {"error": str(e)})

        self.send_json(404, {"error": "Not found"})

    def do_PATCH(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/canvas/items/"):
            item_id = path[len("/api/canvas/items/"):].strip("/")
            body = self.read_json()
            with _STATE_LOCK:
                items = load_items()
                found = None
                for it in items:
                    if it.get("id") == item_id:
                        if "x" in body:
                            it["x"] = int(round(float(body["x"])))
                        if "y" in body:
                            it["y"] = int(round(float(body["y"])))
                        found = it
                        break
                if found:
                    save_items(items)
                    return self.send_json(200, {"ok": True, "item": found})
            return self.send_json(404, {"error": "Item not found"})
        self.send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/canvas/items/"):
            item_id = path[len("/api/canvas/items/"):].strip("/")
            with _STATE_LOCK:
                items = load_items()
                remaining = []
                deleted = False
                for it in items:
                    if it.get("id") == item_id:
                        deleted = True
                        f = OUTPUTS_DIR / it.get("filename", "")
                        if f.exists():
                            f.unlink(missing_ok=True)
                    else:
                        if it.get("parent_id") == item_id:
                            it["parent_id"] = None
                        remaining.append(it)
                if deleted:
                    save_items(remaining)
                    return self.send_json(200, {"ok": True})
            return self.send_json(404, {"error": "Item not found"})
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
    server = ThreadingHTTPServer(("127.0.0.1", port), StudioHandler)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}"
    print("=" * 60)
    print("  Colab Media Studio · 无限画布创作台已启动")
    print(f"  本地地址: {url}")
    print(f"  作品目录: {OUTPUTS_DIR}")
    print("  无需打开 Google Colab 网页，全部在本地 App 中自动托管运行")
    print("=" * 60)

    if not no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已关闭 Colab Media Studio。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
