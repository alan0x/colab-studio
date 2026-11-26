"""
Colab GPU Lifecycle & Quota Manager powered by official `google-colab-cli`.
Completely eliminates the need to open the Google Colab web UI:
- In-app Google OAuth2 sign-in (persisted securely in macOS Keychain via colab-cli)
- Real-time Compute Units balance, burn rate, and remaining hours calculation
- On-demand GPU provisioning (L4 / T4 / A100) with smart fallback
- Direct file upload, live-streaming remote execution, and artifact download
- Automatic idle shutdown guard to prevent burning Compute Units
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

SESSION_NAME = "studio"
REMOTE_WORKER_PATH = Path(__file__).resolve().parent / "remote_worker.py"

GPU_EST_RATES = {
    "T4": 1.2,
    "L4": 2.2,
    "G4": 3.5,
    "A100": 6.0,
    "H100": 18.0,
}

GPU_VRAM_LABELS = {
    "T4": "16 GB",
    "L4": "24 GB",
    "G4": "24 GB",
    "A100": "40/80 GB",
    "H100": "80 GB",
}


def find_colab_bin() -> Optional[str]:
    found = shutil.which("colab")
    if found:
        return found
    candidates = [
        Path.home() / ".local/bin/colab",
        Path("/opt/homebrew/bin/colab"),
        Path("/usr/local/bin/colab"),
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return None


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    extra_paths = [
        str(Path.home() / ".local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    env["PATH"] = ":".join(extra_paths + [env.get("PATH", "")])
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text)


class ColabManager:
    def __init__(self, get_settings: Callable[[], dict[str, Any]]):
        self.get_settings = get_settings
        self._lock = threading.Lock()
        self._gpu_lock = threading.Lock()
        self._oauth_proc: Optional[subprocess.Popen[str]] = None
        self._oauth_url: Optional[str] = None

        self.authenticated: bool = False
        self.user_email: Optional[str] = None
        self.usage: dict[str, Any] = {
            "balance_cu": None,
            "usage_rate_hourly": 0.0,
            "assignments_count": 0,
            "remaining_hours_live": None,
            "est_hours_by_gpu": {},
        }
        self.session: dict[str, Any] = {
            "active": False,
            "name": SESSION_NAME,
            "accelerator": None,
            "vram_label": None,
            "machine_shape": None,
            "status": "OFFLINE",
            "gpu_name_detail": None,
            "vram_total_gb": None,
            "vram_used_gb": None,
            "booted_at": None,
        }
        self.active_job_id: Optional[str] = None
        self._active_proc: Optional[subprocess.Popen[str]] = None
        self._cancel_requested: bool = False
        self.last_activity_at: float = time.time()
        self.last_refresh_at: float = 0.0
        self.last_error: Optional[str] = None
        self._bootstrapped_endpoint: Optional[str] = None

        # Start idle auto-shutdown guard
        threading.Thread(target=self._idle_guard_loop, daemon=True).start()

    def _run(
        self,
        args: list[str],
        timeout: float = 20.0,
        stdin_devnull: bool = True,
    ) -> tuple[int, str, str]:
        colab_bin = find_colab_bin()
        if not colab_bin:
            return 127, "", "未检测到 google-colab-cli，请在终端运行 `uv tool install google-colab-cli`。"
        cmd = [colab_bin, "--auth=oauth2", *args]
        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL if stdin_devnull else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_clean_env(),
            )
            return proc.returncode, _strip_ansi(proc.stdout or ""), _strip_ansi(proc.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, "", f"执行超时 ({timeout}s)"
        except Exception as e:
            return 1, "", str(e)

    # --------------------------------------------------------
    # In-App Google OAuth2 Sign-In
    # --------------------------------------------------------
    def start_oauth_login(self) -> dict[str, Any]:
        colab_bin = find_colab_bin()
        if not colab_bin:
            raise RuntimeError("未检测到 google-colab-cli，请先安装。")

        with self._lock:
            self._cleanup_oauth_proc()
            proc = subprocess.Popen(
                [colab_bin, "--auth=oauth2", "whoami"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=_clean_env(),
            )
            self._oauth_proc = proc
            self._oauth_url = None

        auth_url = None
        collected_err: list[str] = []
        deadline = time.time() + 15.0

        def _reader():
            nonlocal auth_url
            assert proc.stderr is not None
            for line in proc.stderr:
                clean = _strip_ansi(line).strip()
                collected_err.append(clean)
                m = re.search(r"(https://accounts\.google\.com/\S+)", clean)
                if m:
                    auth_url = m.group(1)
                    break

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        while time.time() < deadline and auth_url is None and proc.poll() is None:
            time.sleep(0.1)

        if auth_url:
            with self._lock:
                self._oauth_url = auth_url
            return {"already_authenticated": False, "auth_url": auth_url}

        # Maybe already authenticated!
        if proc.poll() == 0:
            out = _strip_ansi(proc.stdout.read() if proc.stdout else "")
            self._cleanup_oauth_proc()
            self.refresh_state(force=True)
            return {"already_authenticated": True, "email": self.user_email, "raw": out}

        self._cleanup_oauth_proc()
        raise RuntimeError("无法获取 Google 授权链接：" + " ".join(collected_err[-3:]))

    def complete_oauth_login(self, code: str) -> dict[str, Any]:
        code = code.strip()
        if not code:
            raise ValueError("请先粘贴 Google 授权码。")

        with self._lock:
            proc = self._oauth_proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise RuntimeError("授权会话已过期，请重新点击「打开 Google 授权页」。")

        try:
            proc.stdin.write(code + "\n")
            proc.stdin.flush()
            stdout, stderr = proc.communicate(timeout=25)
        except subprocess.TimeoutExpired:
            self._cleanup_oauth_proc()
            raise RuntimeError("验证授权码超时，请重试。")
        finally:
            self._cleanup_oauth_proc()

        if proc.returncode != 0:
            msg = _strip_ansi(stderr or stdout).strip()
            raise RuntimeError(f"授权失败，请检查授权码是否完整有效：{msg[-240:]}")

        self.refresh_state(force=True)
        return {
            "ok": True,
            "authenticated": self.authenticated,
            "email": self.user_email,
            "usage": self.usage,
        }

    def _cleanup_oauth_proc(self) -> None:
        if self._oauth_proc is not None:
            try:
                if self._oauth_proc.poll() is None:
                    self._oauth_proc.terminate()
            except Exception:
                pass
            self._oauth_proc = None
            self._oauth_url = None

    # --------------------------------------------------------
    # Live Telemetry: Auth, Compute Units Balance, Active GPU
    # --------------------------------------------------------
    def refresh_state(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and (now - self.last_refresh_at) < 4.0:
            return self.get_snapshot()

        colab_bin = find_colab_bin()
        if not colab_bin:
            self.authenticated = False
            self.last_error = "未安装 google-colab-cli"
            return self.get_snapshot()

        # 1. Check auth & email via whoami (fast, non-blocking)
        rc, out, err = self._run(["whoami"], timeout=10.0)
        if rc != 0:
            self.authenticated = False
            self.user_email = None
            self.session["active"] = False
            self.session["status"] = "OFFLINE"
            self.last_refresh_at = time.time()
            self.last_error = None
            return self.get_snapshot()

        self.authenticated = True
        m_email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", out)
        if m_email:
            self.user_email = m_email.group(0)

        # 2. Fetch real Compute Units balance & burn rate via `colab usage`
        rc_u, out_u, err_u = self._run(["usage"], timeout=12.0)
        if rc_u == 0:
            self._parse_usage(out_u)

        # 3. Fetch active session & hardware via `colab status -s studio` and `sessions`
        rc_s, out_s, _ = self._run(["status", "-s", SESSION_NAME], timeout=12.0)
        parsed_active = False
        if rc_s == 0 and f"[{SESSION_NAME}]" in out_s:
            parsed_active = self._parse_session_line(out_s)
        else:
            # Check if there is any other active session on server
            rc_all, out_all, _ = self._run(["sessions"], timeout=12.0)
            if rc_all == 0 and f"[{SESSION_NAME}]" in out_all:
                parsed_active = self._parse_session_line(out_all)

        if not parsed_active:
            self.session.update(
                {
                    "active": False,
                    "accelerator": None,
                    "vram_label": None,
                    "machine_shape": None,
                    "status": "OFFLINE",
                    "gpu_name_detail": None,
                    "vram_total_gb": None,
                    "vram_used_gb": None,
                    "booted_at": None,
                }
            )
            self._bootstrapped_endpoint = None

        self.last_refresh_at = time.time()
        self.last_error = None
        return self.get_snapshot()

    def _parse_usage(self, text: str) -> None:
        m_bal = re.search(r"Current balance:\s*([\d.]+)", text)
        m_rate = re.search(r"Usage rate:\s*([\d.]+)/hr", text)
        m_cnt = re.search(r"Active assignments:\s*(\d+)", text)

        bal = float(m_bal.group(1)) if m_bal else self.usage.get("balance_cu")
        rate = float(m_rate.group(1)) if m_rate else 0.0
        cnt = int(m_cnt.group(1)) if m_cnt else 0

        est_by_gpu = {}
        if bal is not None:
            for gpu, r in GPU_EST_RATES.items():
                est_by_gpu[gpu] = round(bal / r, 1)

        rem_live = round(bal / rate, 1) if (bal is not None and rate > 0.01) else None
        self.usage = {
            "balance_cu": round(bal, 2) if bal is not None else None,
            "usage_rate_hourly": round(rate, 2),
            "assignments_count": cnt,
            "remaining_hours_live": rem_live,
            "est_hours_by_gpu": est_by_gpu,
        }

    def _parse_session_line(self, text: str) -> bool:
        for line in text.splitlines():
            if f"[{SESSION_NAME}]" not in line:
                continue
            m_ep = re.search(r"\[[^\]]+\]\s+(\S+)", line)
            m_hw = re.search(r"Hardware:\s*([^|]+)", line)
            m_sh = re.search(r"Shape:\s*([^|]+)", line)
            m_st = re.search(r"Status:\s*([^|]+)", line)

            hw = m_hw.group(1).strip() if m_hw else "GPU"
            ep = m_ep.group(1).strip() if m_ep else None
            was_active = self.session.get("active")
            self.session.update(
                {
                    "active": True,
                    "endpoint": ep,
                    "accelerator": hw,
                    "vram_label": GPU_VRAM_LABELS.get(hw.upper(), ""),
                    "machine_shape": m_sh.group(1).strip() if m_sh else "STANDARD",
                    "status": m_st.group(1).strip() if m_st else ("BUSY" if self.active_job_id else "IDLE"),
                    "booted_at": self.session.get("booted_at") or (time.time() if not was_active else time.time()),
                }
            )
            return True
        return False

    def get_snapshot(self) -> dict[str, Any]:
        settings = self.get_settings()
        auto_stop_min = int(settings.get("auto_stop_minutes", 10) or 0)
        idle_sec = 0.0
        auto_stop_remaining = None
        if self.session.get("active") and not self.active_job_id:
            idle_sec = max(0.0, time.time() - self.last_activity_at)
            if auto_stop_min > 0:
                auto_stop_remaining = max(0, int(auto_stop_min * 60 - idle_sec))

        return {
            "cli_installed": find_colab_bin() is not None,
            "authenticated": self.authenticated,
            "user_email": self.user_email,
            "usage": self.usage,
            "session": self.session,
            "active_job_id": self.active_job_id,
            "idle_seconds": round(idle_sec, 1),
            "auto_stop_minutes": auto_stop_min,
            "auto_stop_remaining_seconds": auto_stop_remaining,
            "preferred_gpu": settings.get("preferred_gpu", "L4"),
            "last_error": self.last_error,
        }

    # --------------------------------------------------------
    # GPU Provisioning & Release
    # --------------------------------------------------------
    def ensure_session(
        self,
        preferred_gpu: str = "L4",
        progress_cb: Optional[Callable[[str, int], None]] = None,
    ) -> dict[str, Any]:
        self.refresh_state(force=True)
        if not self.authenticated:
            raise RuntimeError("请先点击右上角「登录 Google 账号」完成一次授权。")

        if self.session.get("active"):
            return self.session

        order_map = {
            "A100": ["A100", "L4", "T4"],
            "L4": ["L4", "T4"],
            "T4": ["T4"],
            "G4": ["G4", "L4", "T4"],
            "H100": ["H100", "A100", "L4", "T4"],
        }
        candidates = order_map.get(preferred_gpu.upper(), ["L4", "T4"])
        errors = []

        for idx, gpu in enumerate(candidates):
            if progress_cb:
                msg = f"正在向 Google Colab 申请 {gpu} 云端显卡..." if idx == 0 else f"{candidates[idx-1]} 暂不可用，自动降级申请 {gpu} 显卡..."
                progress_cb(msg, 4 + idx * 2)

            arg_variants = [["new", "-s", SESSION_NAME, "--gpu", gpu]]
            if gpu in ("A100", "T4"):
                arg_variants.insert(0, ["new", "-s", SESSION_NAME, "--gpu", gpu, "--high-mem"])

            rc = 1
            out, err = "", ""
            for args in arg_variants:
                rc, out, err = self._run(args, timeout=75.0)
                if rc == 0:
                    break
            if rc == 0:
                self.last_activity_at = time.time()
                self._bootstrapped_endpoint = None
                self.refresh_state(force=True)
                self.session["booted_at"] = time.time()
                return self.session

            err_text = (err or out).strip()
            errors.append(f"{gpu}: {err_text[:180]}")

            # If too many assignments, don't keep retrying different GPUs blindly
            if "precondition failed" in err_text.lower() or "too many" in err_text.lower():
                raise RuntimeError(
                    "你的 Google Colab 账号已有过多运行中的会话（可能之前网页端未断开）。"
                    "请先释放旧会话后再试。"
                )

        raise RuntimeError("未能分配到云端 GPU：\n" + "\n".join(errors))

    def start_gpu(self, gpu: str = "L4") -> dict[str, Any]:
        with self._gpu_lock:
            # If a different GPU is currently running, stop it first
            self.refresh_state(force=True)
            if self.session.get("active"):
                cur = (self.session.get("accelerator") or "").upper()
                if cur == gpu.upper():
                    return self.get_snapshot()
                self._run(["stop", "-s", SESSION_NAME], timeout=30.0)
            self.ensure_session(preferred_gpu=gpu)
            return self.get_snapshot()

    def stop_gpu(self) -> dict[str, Any]:
        with self._gpu_lock:
            self._run(["stop", "-s", SESSION_NAME], timeout=30.0)
            self._bootstrapped_endpoint = None
            self.session["active"] = False
            self.session["status"] = "OFFLINE"
            self.session["booted_at"] = None
            time.sleep(1.0)
            self.refresh_state(force=True)
            return self.get_snapshot()

    # --------------------------------------------------------
    # Direct Remote Bootstrap & Inference Execution
    # --------------------------------------------------------
    def _ensure_bootstrapped(self, progress_cb: Callable[[str, int], None]) -> None:
        ep = self.session.get("endpoint")
        if ep and self._bootstrapped_endpoint == ep:
            return

        progress_cb("正在检查云端推理环境（同一次开机仅首次初始化）...", 10)
        check_code = (
            "import importlib.util\n"
            "ok = all(importlib.util.find_spec(m) for m in ('diffusers','transformers','accelerate','imageio_ffmpeg','hf_transfer'))\n"
            "print('ENV_OK' if ok else 'ENV_MISSING')\n"
        )
        rc, out, _ = self._exec_inline(check_code, timeout=45.0)
        if rc == 0 and "ENV_OK" in out:
            self._bootstrapped_endpoint = ep
            return

        progress_cb("首次开机：正在极速安装 AI 视频/图像引擎（约 30-60 秒）...", 14)
        install_pkgs = [
            "install", "-s", SESSION_NAME,
            "accelerate", "transformers", "sentencepiece", "protobuf",
            "imageio", "imageio-ffmpeg", "hf_transfer", "pillow",
            "git+https://github.com/huggingface/diffusers.git",
        ]
        rc_i, out_i, err_i = self._run(install_pkgs, timeout=360.0)
        if rc_i != 0:
            raise RuntimeError(f"云端环境安装失败：{(err_i or out_i)[-300:]}")
        self._bootstrapped_endpoint = ep

    def _exec_inline(self, code: str, timeout: float = 45.0) -> tuple[int, str, str]:
        colab_bin = find_colab_bin()
        if not colab_bin:
            return 127, "", "colab CLI 未安装"
        try:
            proc = subprocess.run(
                [colab_bin, "--auth=oauth2", "exec", "-s", SESSION_NAME, "--timeout", str(timeout)],
                input=code,
                capture_output=True,
                text=True,
                timeout=timeout + 15.0,
                env=_clean_env(),
            )
            return proc.returncode, _strip_ansi(proc.stdout or ""), _strip_ansi(proc.stderr or "")
        except Exception as e:
            return 1, "", str(e)

    def cancel_active_job(self) -> dict[str, Any]:
        self._cancel_requested = True
        proc = self._active_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        if self.session.get("active"):
            # Interrupt & clear remote GPU kernel in background without destroying the warm VM
            threading.Thread(
                target=lambda: self._run(["restart-kernel", "-s", SESSION_NAME], timeout=25.0),
                daemon=True,
            ).start()
        return {"ok": True}

    def run_generation(
        self,
        job_id: str,
        params: dict[str, Any],
        init_image_local: Optional[Path],
        output_local_path: Path,
        on_update: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        with self._gpu_lock:
            self.active_job_id = job_id
            self._cancel_requested = False
            self.last_activity_at = time.time()
            try:
                settings = self.get_settings()
                preferred_gpu = params.get("gpu") or settings.get("preferred_gpu", "L4")

                # 1. Ensure GPU VM is running
                self.ensure_session(
                    preferred_gpu=preferred_gpu,
                    progress_cb=lambda msg, pct: on_update({"status": "booting", "stage": msg, "progress": pct}),
                )
                if self._cancel_requested:
                    raise RuntimeError("任务已取消")

                # 2. Ensure diffusers & codecs are installed once per VM
                self._ensure_bootstrapped(
                    progress_cb=lambda msg, pct: on_update({"status": "preparing", "stage": msg, "progress": pct})
                )
                if self._cancel_requested:
                    raise RuntimeError("任务已取消")

                # 3. Upload reference material if Image-to-Video
                remote_input_path = None
                if init_image_local and init_image_local.exists():
                    on_update({"status": "uploading", "stage": "正在上传本地参考素材到云端 GPU...", "progress": 18})
                    remote_input_path = f"/content/studio_in_{job_id[:8]}{init_image_local.suffix or '.png'}"
                    rc_up, out_up, err_up = self._run(
                        ["upload", "-s", SESSION_NAME, str(init_image_local), remote_input_path],
                        timeout=60.0,
                    )
                    if rc_up != 0:
                        raise RuntimeError(f"上传参考图片失败：{(err_up or out_up)[-240:]}")

                # 4. Run remote worker script & stream progress live
                job_payload = {
                    **params,
                    "job_id": job_id,
                    "remote_input_path": remote_input_path,
                    "remote_output_base": f"/content/studio_out_{job_id[:8]}",
                }
                payload_b64 = base64.b64encode(json.dumps(job_payload).encode("utf-8")).decode("ascii")

                colab_bin = find_colab_bin()
                assert colab_bin is not None
                cmd = [
                    colab_bin,
                    "--auth=oauth2",
                    "exec",
                    "-s",
                    SESSION_NAME,
                    "--timeout",
                    "0",
                    "--env",
                    f"STUDIO_JOB_B64={payload_b64}",
                    "-f",
                    str(REMOTE_WORKER_PATH),
                ]
                on_update({"status": "running", "stage": "正在唤醒云端模型并分配显存...", "progress": 20})

                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=_clean_env(),
                )
                self._active_proc = proc

                result_meta: Optional[dict[str, Any]] = None
                recent_logs: list[str] = []

                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    if self._cancel_requested:
                        break
                    line = _strip_ansi(raw_line).strip()
                    if not line:
                        continue
                    if line.startswith("@@PROGRESS "):
                        try:
                            info = json.loads(line[len("@@PROGRESS "):])
                            if info.get("gpu_name"):
                                self.session["gpu_name_detail"] = info["gpu_name"]
                            if info.get("vram_total_gb"):
                                self.session["vram_total_gb"] = info["vram_total_gb"]
                            if info.get("vram_used_gb") is not None:
                                self.session["vram_used_gb"] = info["vram_used_gb"]
                            on_update(info)
                        except Exception:
                            pass
                    elif line.startswith("@@RESULT "):
                        try:
                            result_meta = json.loads(line[len("@@RESULT "):])
                        except Exception:
                            pass
                    else:
                        recent_logs.append(line)
                        if len(recent_logs) > 25:
                            recent_logs.pop(0)

                stderr_text = _strip_ansi(proc.stderr.read() if proc.stderr else "").strip()
                rc = proc.wait()
                self._active_proc = None

                if self._cancel_requested:
                    raise RuntimeError("任务已取消")

                if rc != 0 or not result_meta or not result_meta.get("remote_output"):
                    err_detail = (
                        (result_meta or {}).get("error")
                        or stderr_text[-400:]
                        or "\n".join(recent_logs[-8:])
                        or f"远程执行失败 (exit {rc})"
                    )
                    # Clean dead session state if backend pruned VM
                    if "not found" in err_detail.lower() or "404" in err_detail:
                        self.session["active"] = False
                        self._bootstrapped_endpoint = None
                    raise RuntimeError(err_detail)

                # 5. Download finished artifact directly back to Mac local storage
                on_update({"status": "downloading", "stage": "生成完成！正在回传高清成品到本地画布...", "progress": 96})
                remote_out = result_meta["remote_output"]
                ext = result_meta.get("file_ext", "png").lstrip(".")
                final_local = output_local_path.with_suffix(f".{ext}")
                rc_dl, out_dl, err_dl = self._run(
                    ["download", "-s", SESSION_NAME, remote_out, str(final_local)],
                    timeout=180.0,
                )
                if rc_dl != 0 or not final_local.exists():
                    raise RuntimeError(f"下载云端成品失败：{(err_dl or out_dl)[-240:]}")

                result_meta["local_path"] = str(final_local)
                result_meta["filename"] = final_local.name
                return result_meta
            finally:
                self._active_proc = None
                self.active_job_id = None
                self._cancel_requested = False
                self.last_activity_at = time.time()
                try:
                    self.refresh_state(force=True)
                except Exception:
                    pass

    # --------------------------------------------------------
    # Idle Compute-Unit Saver Daemon
    # --------------------------------------------------------
    def _idle_guard_loop(self) -> None:
        while True:
            time.sleep(20)
            try:
                if not self.session.get("active") or self.active_job_id:
                    continue
                settings = self.get_settings()
                auto_stop_min = int(settings.get("auto_stop_minutes", 10) or 0)
                if auto_stop_min <= 0:
                    continue
                idle_sec = time.time() - self.last_activity_at
                if idle_sec >= auto_stop_min * 60:
                    print(f"[省单元守护] Colab GPU 已空闲 {auto_stop_min} 分钟，自动释放算力停止计费...")
                    self.stop_gpu()
            except Exception:
                pass
