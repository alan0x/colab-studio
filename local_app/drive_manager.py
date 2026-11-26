"""
Google Drive Cloud Storage & Sync Manager for Colab Canvas Studio.

Reuses the exact OAuth2 token stored by official `google-colab-cli`
(which already requests the `https://www.googleapis.com/auth/drive.file` scope),
so users need ZERO extra login or browser tabs.

Cloud folder layout in user's Google Drive (My Drive):
  ColabCanvasStudio/
    ├── materials/      # Uploaded reference images
    ├── outputs/        # Generated AI images (.png) & videos (.mp4)
    └── canvas.json     # Synced infinite canvas metadata & node positions
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from colab_manager import find_colab_bin

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
ROOT_FOLDER_NAME = "ColabCanvasStudio"
_SUBPROC_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_TOKEN_HELPER_CODE = r"""
import json, sys
from colab_cli import auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

raw = auth._get_token_from_keyring()
if not raw:
    sys.exit(2)
creds = Credentials.from_authorized_user_info(json.loads(raw), auth.PUBLIC_SCOPES)
if not creds.valid:
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        auth._save_token_to_keyring(creds.to_json())
    else:
        sys.exit(3)
exp = creds.expiry.timestamp() if creds.expiry else 0
print(json.dumps({"token": creds.token, "expiry": exp, "scopes": list(creds.scopes or [])}))
"""


def _get_token_in_process() -> Optional[tuple[str, float]]:
    """Fast path when `colab_cli` is installed/bundled in the current Python runtime."""
    try:
        from colab_cli import auth
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        raw = auth._get_token_from_keyring()
        if not raw:
            return None
        creds = Credentials.from_authorized_user_info(json.loads(raw), auth.PUBLIC_SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                auth._save_token_to_keyring(creds.to_json())
            else:
                return None
        if not creds.token:
            return None
        exp = creds.expiry.timestamp() if creds.expiry else 0.0
        return creds.token, exp
    except Exception:
        return None


def _find_colab_python() -> Optional[str]:
    if sys.platform == "win32":
        candidates = [
            Path(sys.executable).parent / "python.exe",
        ]
        for base_env in ("APPDATA", "LOCALAPPDATA"):
            base_dir = os.environ.get(base_env)
            if base_dir:
                candidates.append(Path(base_dir) / "uv/tools/google-colab-cli/Scripts/python.exe")
        candidates.append(Path.home() / ".local/share/uv/tools/google-colab-cli/Scripts/python.exe")
        for c in candidates:
            if c.exists():
                return str(c)
        return None

    colab_bin = find_colab_bin()
    if not colab_bin:
        return None
    try:
        first_line = Path(colab_bin).read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
        if first_line.startswith("#!"):
            py = first_line[2:].strip().split()[0]
            if Path(py).exists():
                return py
    except Exception:
        pass
    return None


class DriveManager:
    def __init__(self, outputs_dir: Path, get_settings: Callable[[], dict[str, Any]]):
        self.outputs_dir = outputs_dir
        self.get_settings = get_settings
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()

        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

        self.root_folder_id: Optional[str] = None
        self.materials_folder_id: Optional[str] = None
        self.outputs_folder_id: Optional[str] = None
        self.canvas_json_file_id: Optional[str] = None

        self.connected: bool = False
        self.syncing: bool = False
        self.last_sync_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self._canvas_timer: Optional[threading.Timer] = None

    def is_enabled(self) -> bool:
        return bool(self.get_settings().get("drive_sync_enabled", True))

    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        with self._lock:
            now = time.time()
            if not force_refresh and self._token and (self._token_expiry == 0 or now < self._token_expiry - 120):
                return self._token

        # 1. Try direct in-process keyring token (used in bundled Windows app & shared venvs)
        in_proc = _get_token_in_process()
        if in_proc:
            tok, exp = in_proc
            with self._lock:
                self._token = tok
                self._token_expiry = exp if exp > time.time() else (time.time() + 2400)
            return tok

        # 2. Fallback to colab-cli's Python interpreter (e.g. uv tool isolated env on macOS/Linux)
        py = _find_colab_python()
        if not py:
            return None

        try:
            proc = subprocess.run(
                [py, "-c", _TOKEN_HELPER_CODE],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20.0,
                creationflags=_SUBPROC_FLAGS,
            )
            if proc.returncode != 0:
                return None
            info = json.loads(proc.stdout.strip())
            tok = info.get("token")
            exp = float(info.get("expiry") or 0)
            if not tok:
                return None
            with self._lock:
                self._token = tok
                self._token_expiry = exp if exp > time.time() else (time.time() + 2400)
            return tok
        except Exception:
            return None

    def clear_cached_token(self) -> None:
        with self._lock:
            self._token = None
            self._token_expiry = 0.0
            self.connected = False

    def _drive_request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        expect_bytes: bool = False,
        retry_auth: bool = True,
    ) -> tuple[int, Any, dict[str, str]]:
        token = self.get_access_token()
        if not token:
            raise RuntimeError("尚未登录 Google 账号，无法访问 Google Drive。")

        req_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        data = raw_body
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json; charset=utf-8")

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                raw = resp.read()
                if expect_bytes:
                    return resp.status, raw, resp_headers
                if not raw:
                    return resp.status, {}, resp_headers
                try:
                    return resp.status, json.loads(raw.decode("utf-8")), resp_headers
                except Exception:
                    return resp.status, raw.decode("utf-8", errors="replace"), resp_headers
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry_auth:
                self.get_access_token(force_refresh=True)
                return self._drive_request(
                    method,
                    url,
                    json_body=json_body,
                    raw_body=raw_body,
                    headers=headers,
                    timeout=timeout,
                    expect_bytes=expect_bytes,
                    retry_auth=False,
                )
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Google Drive API 错误 ({e.code}): {body[:280]}")

    def _find_or_create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        q_parts = [
            f"name = '{name}'",
            f"mimeType = '{FOLDER_MIME}'",
            "trashed = false",
        ]
        if parent_id:
            q_parts.append(f"'{parent_id}' in parents")
        else:
            q_parts.append("'root' in parents")
        q = urllib.parse.quote(" and ".join(q_parts))
        _, data, _ = self._drive_request("GET", f"{DRIVE_API}/files?q={q}&fields=files(id,name)&spaces=drive")
        files = data.get("files", [])
        if files:
            return files[0]["id"]

        meta: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            meta["parents"] = [parent_id]
        _, created, _ = self._drive_request("POST", f"{DRIVE_API}/files?fields=id,name", json_body=meta)
        return created["id"]

    def ensure_folders(self) -> dict[str, str]:
        if self.root_folder_id and self.materials_folder_id and self.outputs_folder_id:
            self.connected = True
            return {
                "root": self.root_folder_id,
                "materials": self.materials_folder_id,
                "outputs": self.outputs_folder_id,
            }
        root_id = self._find_or_create_folder(ROOT_FOLDER_NAME)
        mat_id = self._find_or_create_folder("materials", parent_id=root_id)
        out_id = self._find_or_create_folder("outputs", parent_id=root_id)
        self.root_folder_id = root_id
        self.materials_folder_id = mat_id
        self.outputs_folder_id = out_id
        self.connected = True
        self.last_error = None
        return {"root": root_id, "materials": mat_id, "outputs": out_id}

    def upload_file(self, local_path: Path, folder_kind: str = "outputs") -> dict[str, Any]:
        """Upload a local image or video using Google Drive Resumable Upload API."""
        folders = self.ensure_folders()
        parent_id = folders["materials"] if folder_kind == "materials" else folders["outputs"]
        mime, _ = mimetypes.guess_type(str(local_path))
        mime = mime or "application/octet-stream"
        file_bytes = local_path.read_bytes()

        # Step 1: Initiate resumable upload session
        init_url = f"{DRIVE_UPLOAD_API}/files?uploadType=resumable&fields=id,name,webViewLink,size"
        _, _, resp_headers = self._drive_request(
            "POST",
            init_url,
            json_body={"name": local_path.name, "parents": [parent_id]},
            headers={
                "X-Upload-Content-Type": mime,
                "X-Upload-Content-Length": str(len(file_bytes)),
            },
            timeout=30.0,
        )
        upload_uri = resp_headers.get("location")
        if not upload_uri:
            raise RuntimeError("Google Drive 未返回上传会话地址。")

        # Step 2: Upload file payload
        _, uploaded, _ = self._drive_request(
            "PUT",
            upload_uri,
            raw_body=file_bytes,
            headers={
                "Content-Type": mime,
                "Content-Length": str(len(file_bytes)),
            },
            timeout=300.0,
        )
        self.last_sync_at = time.time()
        self.last_error = None
        return {
            "drive_file_id": uploaded["id"],
            "drive_web_link": uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{uploaded['id']}/view",
        }

    def download_file(self, file_id: str, dest_path: Path) -> Path:
        _, raw, _ = self._drive_request(
            "GET",
            f"{DRIVE_API}/files/{urllib.parse.quote(file_id)}?alt=media",
            timeout=180.0,
            expect_bytes=True,
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(raw)
        return dest_path

    def trash_file(self, file_id: str) -> None:
        if not file_id:
            return
        try:
            self._drive_request(
                "PATCH",
                f"{DRIVE_API}/files/{urllib.parse.quote(file_id)}",
                json_body={"trashed": True},
                timeout=20.0,
            )
        except Exception as e:
            self.last_error = str(e)

    def _find_canvas_json_id(self) -> Optional[str]:
        if self.canvas_json_file_id:
            return self.canvas_json_file_id
        folders = self.ensure_folders()
        q = urllib.parse.quote(
            f"name = 'canvas.json' and '{folders['root']}' in parents and trashed = false"
        )
        _, data, _ = self._drive_request("GET", f"{DRIVE_API}/files?q={q}&fields=files(id,name)")
        files = data.get("files", [])
        if files:
            self.canvas_json_file_id = files[0]["id"]
            return self.canvas_json_file_id
        return None

    def save_canvas_manifest(self, items: list[dict[str, Any]]) -> None:
        if not self.is_enabled():
            return
        if not self.get_access_token():
            return
        try:
            folders = self.ensure_folders()
            payload = json.dumps(items, indent=2, ensure_ascii=False).encode("utf-8")
            fid = self._find_canvas_json_id()
            if fid:
                self._drive_request(
                    "PATCH",
                    f"{DRIVE_UPLOAD_API}/files/{urllib.parse.quote(fid)}?uploadType=media",
                    raw_body=payload,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    timeout=30.0,
                )
            else:
                _, _, resp_headers = self._drive_request(
                    "POST",
                    f"{DRIVE_UPLOAD_API}/files?uploadType=resumable&fields=id",
                    json_body={"name": "canvas.json", "parents": [folders["root"]]},
                    headers={
                        "X-Upload-Content-Type": "application/json",
                        "X-Upload-Content-Length": str(len(payload)),
                    },
                    timeout=30.0,
                )
                upload_uri = resp_headers.get("location")
                if upload_uri:
                    _, created, _ = self._drive_request(
                        "PUT",
                        upload_uri,
                        raw_body=payload,
                        headers={"Content-Type": "application/json; charset=utf-8"},
                        timeout=30.0,
                    )
                    self.canvas_json_file_id = created.get("id")
            self.last_sync_at = time.time()
            self.last_error = None
        except Exception as e:
            self.last_error = str(e)

    def schedule_manifest_sync(self, get_items: Callable[[], list[dict[str, Any]]], delay: float = 2.0) -> None:
        if not self.is_enabled():
            return
        with self._lock:
            if self._canvas_timer:
                self._canvas_timer.cancel()
            t = threading.Timer(delay, lambda: self.save_canvas_manifest(get_items()))
            t.daemon = True
            self._canvas_timer = t
            t.start()

    def load_canvas_manifest(self) -> list[dict[str, Any]]:
        fid = self._find_canvas_json_id()
        if not fid:
            return []
        _, raw, _ = self._drive_request(
            "GET",
            f"{DRIVE_API}/files/{urllib.parse.quote(fid)}?alt=media",
            timeout=30.0,
            expect_bytes=True,
        )
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, list) else []

    def sync_all(
        self,
        load_local_items: Callable[[], list[dict[str, Any]]],
        save_local_items: Callable[[list[dict[str, Any]]], None],
    ) -> dict[str, Any]:
        """Two-way sync between local cache and Google Drive."""
        if not self.get_access_token():
            raise RuntimeError("请先登录 Google 账号后再同步 Google Drive。")

        with self._sync_lock:
            self.syncing = True
            uploaded_count = 0
            downloaded_count = 0
            try:
                self.ensure_folders()
                cloud_items = self.load_canvas_manifest()
                local_items = load_local_items()

                merged_by_id: dict[str, dict[str, Any]] = {}
                for it in cloud_items:
                    if it.get("id"):
                        merged_by_id[it["id"]] = dict(it)
                for it in local_items:
                    if it.get("id"):
                        if it["id"] in merged_by_id:
                            # Prefer local position updates while preserving drive_file_id
                            merged = {**merged_by_id[it["id"]], **it}
                            if not merged.get("drive_file_id") and merged_by_id[it["id"]].get("drive_file_id"):
                                merged["drive_file_id"] = merged_by_id[it["id"]]["drive_file_id"]
                                merged["drive_web_link"] = merged_by_id[it["id"]].get("drive_web_link")
                                merged["drive_sync"] = "synced"
                            merged_by_id[it["id"]] = merged
                        else:
                            merged_by_id[it["id"]] = dict(it)

                merged_list = sorted(
                    merged_by_id.values(),
                    key=lambda x: x.get("created_at", ""),
                    reverse=True,
                )

                final_list: list[dict[str, Any]] = []
                for it in merged_list:
                    fname = it.get("filename", "")
                    if not fname:
                        continue
                    local_path = self.outputs_dir / fname

                    # 1. Local exists, not yet on Drive -> Upload to Drive
                    if local_path.exists() and not it.get("drive_file_id"):
                        folder_kind = "materials" if it.get("kind") == "material" else "outputs"
                        info = self.upload_file(local_path, folder_kind=folder_kind)
                        it["drive_file_id"] = info["drive_file_id"]
                        it["drive_web_link"] = info["drive_web_link"]
                        it["drive_sync"] = "synced"
                        uploaded_count += 1
                        final_list.append(it)
                    # 2. Missing locally, exists on Drive -> Download into local playback cache
                    elif not local_path.exists() and it.get("drive_file_id"):
                        try:
                            self.download_file(it["drive_file_id"], local_path)
                            it["local_url"] = f"/outputs/{urllib.parse.quote(fname)}"
                            it["drive_sync"] = "synced"
                            downloaded_count += 1
                            final_list.append(it)
                        except Exception:
                            # File may have been deleted in Drive manually
                            continue
                    elif local_path.exists():
                        if it.get("drive_file_id"):
                            it["drive_sync"] = "synced"
                        final_list.append(it)

                save_local_items(final_list)
                self.save_canvas_manifest(final_list)
                self.last_sync_at = time.time()
                self.last_error = None
                return {
                    "ok": True,
                    "uploaded": uploaded_count,
                    "downloaded": downloaded_count,
                    "total": len(final_list),
                    "folder_url": self.get_folder_url(),
                    "items": final_list,
                }
            except Exception as e:
                self.last_error = str(e)
                raise
            finally:
                self.syncing = False

    def get_folder_url(self) -> Optional[str]:
        if not self.root_folder_id:
            return None
        return f"https://drive.google.com/drive/folders/{self.root_folder_id}"

    def get_status(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        synced_count = sum(1 for it in items if it.get("drive_file_id"))
        return {
            "enabled": self.is_enabled(),
            "connected": self.connected,
            "syncing": self.syncing,
            "folder_name": ROOT_FOLDER_NAME,
            "folder_url": self.get_folder_url(),
            "synced_count": synced_count,
            "total_count": len(items),
            "last_sync_at": self.last_sync_at,
            "last_error": self.last_error,
        }
