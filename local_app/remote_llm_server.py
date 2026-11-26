"""
Remote Multimodal LLM Server Manager — executed inside the persistent Google Colab
Jupyter kernel via `colab exec -s studio -f remote_llm_server.py`.

Manages a prebuilt CUDA `llama-server` (llama.cpp) + `cloudflared` HTTPS tunnel
serving verified ungated open-source multimodal models (Qwen3.8-27B, Qwen3.5-9B,
Qwen2.5-VL-7B) with native Vision (`mmproj`), Thinking, and OpenAI Tool Calling.

Supports clean VRAM auto-swap: `action="stop"` kills `llama-server` while keeping
all downloaded GGUF files cached on `/content/llm_models/` so switching back from
image/video generation takes only a few seconds.
"""

import base64
import gc
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

import torch

os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

LLAMA_DIR = Path("/content/llama_bin")
MODELS_DIR = Path("/content/llm_models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)

LLM_CATALOG = {
    "qwen3.8-27b": {
        "repo": "unsloth/Qwen3.8-27B-GGUF",
        "gguf": "Qwen3.8-27B-UD-Q4_K_M.gguf",
        "mmproj": "mmproj-F16.gguf",
        "ctx_l4": 24576,
        "ctx_t4": 8192,
    },
    "qwen3.8-27b-t4": {
        "repo": "unsloth/Qwen3.8-27B-GGUF",
        "gguf": "Qwen3.8-27B-UD-Q2_K_XL.gguf",
        "mmproj": "mmproj-F16.gguf",
        "ctx_l4": 24576,
        "ctx_t4": 12288,
    },
    "qwen3.5-9b": {
        "repo": "unsloth/Qwen3.5-9B-GGUF",
        "gguf": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
        "mmproj": "mmproj-F16.gguf",
        "ctx_l4": 32768,
        "ctx_t4": 16384,
    },
    "qwen2.5-vl-7b": {
        "repo": "unsloth/Qwen2.5-VL-7B-Instruct-GGUF",
        "gguf": "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
        "mmproj": "mmproj-F16.gguf",
        "ctx_l4": 32768,
        "ctx_t4": 16384,
    },
}

if "_STUDIO_LLM_STATE" not in globals():
    _STUDIO_LLM_STATE = {
        "llama_proc": None,
        "tunnel_proc": None,
        "public_url": None,
        "api_key": None,
        "model_id": None,
    }


def _emit(tag: str, data: dict) -> None:
    print(f"{tag} {json.dumps(data, ensure_ascii=False)}", flush=True)


def _gpu_stats() -> dict:
    if not torch.cuda.is_available():
        return {"gpu_name": "CPU", "vram_total_gb": 0.0, "vram_used_gb": 0.0}
    return {
        "gpu_name": torch.cuda.get_device_name(0),
        "vram_total_gb": round(torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 1),
        "vram_used_gb": round(torch.cuda.memory_allocated(0) / (1024 ** 3), 2),
    }


def _unload_diffusers_if_loaded() -> None:
    """Free diffusers pipeline VRAM before starting llama-server."""
    st = globals().get("_STUDIO_STATE")
    if isinstance(st, dict) and st.get("pipe") is not None:
        try:
            del st["pipe"]
        except Exception:
            pass
        st["pipe"] = None
        st["model_key"] = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def _stop_llama_server() -> None:
    proc = _STUDIO_LLM_STATE.get("llama_proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    subprocess.run(["pkill", "-f", "llama-server"], check=False)
    _STUDIO_LLM_STATE["llama_proc"] = None
    _STUDIO_LLM_STATE["model_id"] = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _find_llama_server_bin() -> str | None:
    for p in glob.glob(str(LLAMA_DIR / "**" / "llama-server"), recursive=True):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _ensure_llama_binaries() -> str:
    existing = _find_llama_server_bin()
    if existing:
        return existing

    _emit("@@LLM_PROGRESS", {"stage": "正在下载预编译 CUDA llama-server 推理引擎（仅首次约 15 秒）...", "progress": 18})
    LLAMA_DIR.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10",
        headers={"User-Agent": "ColabAgentStudio/2.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        releases = json.loads(r.read().decode("utf-8"))

    llama_url = None
    cudart_url = None
    for rel in releases:
        assets = rel.get("assets", [])
        # Prefer CUDA 12.x Ubuntu x64 binary (matches Colab CUDA 12 drivers)
        for a in assets:
            n = a.get("name", "")
            if re.match(r"^llama-b\d+-bin-ubuntu-cuda-12\.\d+-x64\.tar\.gz$", n):
                llama_url = a["browser_download_url"]
            elif re.match(r"^cudart-llama-b\d+-bin-ubuntu-cuda-12\.\d+-x64\.tar\.gz$", n):
                cudart_url = a["browser_download_url"]
        if llama_url:
            break

    # Fallback to any CUDA 13 or CPU Ubuntu x64 binary if 12.x absent
    if not llama_url:
        for rel in releases:
            for a in rel.get("assets", []):
                n = a.get("name", "")
                if re.match(r"^llama-b\d+-bin-ubuntu-cuda-.*-x64\.tar\.gz$", n):
                    llama_url = a["browser_download_url"]
                elif re.match(r"^cudart-llama-b\d+-bin-ubuntu-cuda-.*-x64\.tar\.gz$", n):
                    cudart_url = a["browser_download_url"]
            if llama_url:
                break

    if not llama_url:
        raise RuntimeError("未能从 llama.cpp GitHub Releases 找到预编译 Ubuntu CUDA 引擎。")

    for label, url in [("llama", llama_url), ("cudart", cudart_url)]:
        if not url:
            continue
        tgz = LLAMA_DIR / f"{label}.tar.gz"
        subprocess.check_call(["wget", "-q", url, "-O", str(tgz)])
        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(LLAMA_DIR)
        tgz.unlink(missing_ok=True)

    # Flatten shared libraries next to llama-server if extracted into subdirs
    bin_path = _find_llama_server_bin()
    if not bin_path:
        raise RuntimeError("解压 llama.cpp 后未找到 llama-server 可执行文件。")
    bin_dir = Path(bin_path).parent
    for so in glob.glob(str(LLAMA_DIR / "**" / "*.so*"), recursive=True):
        dst = bin_dir / Path(so).name
        if not dst.exists():
            try:
                shutil.copy2(so, dst)
            except Exception:
                pass
    os.chmod(bin_path, 0o755)
    return bin_path


def _ensure_cloudflared() -> str:
    cf = "/usr/local/bin/cloudflared"
    if not os.path.exists(cf):
        _emit("@@LLM_PROGRESS", {"stage": "正在配置加密流式传输通道...", "progress": 25})
        subprocess.check_call(
            [
                "wget",
                "-q",
                "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                "-O",
                cf,
            ]
        )
        os.chmod(cf, 0o755)
    return cf


def _ensure_tunnel() -> str:
    proc = _STUDIO_LLM_STATE.get("tunnel_proc")
    url = _STUDIO_LLM_STATE.get("public_url")
    if proc is not None and proc.poll() is None and url:
        return url

    cf = _ensure_cloudflared()
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", "http://127.0.0.1:8080", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    found_url = None
    deadline = time.time() + 40.0
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.15)
            continue
        m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if m:
            found_url = m.group(0)
            break

    if not found_url:
        try:
            proc.terminate()
        except Exception:
            pass
        raise RuntimeError("建立安全流式传输通道失败，请重试。")

    _STUDIO_LLM_STATE["tunnel_proc"] = proc
    _STUDIO_LLM_STATE["public_url"] = found_url
    return found_url


def _start_llm(model_id: str) -> None:
    if model_id not in LLM_CATALOG:
        raise ValueError(f"不支持的对话模型: {model_id}")

    stats = _gpu_stats()
    # Reuse if exact model is already running & healthy
    cur_proc = _STUDIO_LLM_STATE.get("llama_proc")
    if (
        cur_proc is not None
        and cur_proc.poll() is None
        and _STUDIO_LLM_STATE.get("model_id") == model_id
        and _STUDIO_LLM_STATE.get("public_url")
        and _STUDIO_LLM_STATE.get("api_key")
    ):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as r:
                if r.status == 200:
                    _emit(
                        "@@LLM_READY",
                        {
                            "ok": True,
                            "url": _STUDIO_LLM_STATE["public_url"],
                            "api_key": _STUDIO_LLM_STATE["api_key"],
                            "model_id": model_id,
                            **stats,
                        },
                    )
                    return
        except Exception:
            pass

    _unload_diffusers_if_loaded()
    _stop_llama_server()

    spec = LLM_CATALOG[model_id]
    # Auto-downgrade Qwen3.8-27B Q4 -> Q2_K_XL if user got a 16GB T4
    if model_id == "qwen3.8-27b" and stats["vram_total_gb"] < 20.0:
        _emit(
            "@@LLM_PROGRESS",
            {
                "stage": f"检测到当前为 {stats['gpu_name']} ({stats['vram_total_gb']}GB)，自动切换 Qwen3.8-27B 轻量量化版以适配显存...",
                "progress": 16,
            },
        )
        spec = LLM_CATALOG["qwen3.8-27b-t4"]

    llama_bin = _ensure_llama_binaries()

    from huggingface_hub import hf_hub_download

    _emit(
        "@@LLM_PROGRESS",
        {
            "stage": f"正在拉取多模态模型权重 {spec['gguf']}（同一次开机仅下载一次，之后秒切）...",
            "progress": 42,
            **stats,
        },
    )
    gguf_path = hf_hub_download(
        repo_id=spec["repo"],
        filename=spec["gguf"],
        local_dir=str(MODELS_DIR / model_id),
    )

    _emit(
        "@@LLM_PROGRESS",
        {
            "stage": f"正在拉取视觉编码器 {spec['mmproj']}...",
            "progress": 68,
            **stats,
        },
    )
    mmproj_path = hf_hub_download(
        repo_id=spec["repo"],
        filename=spec["mmproj"],
        local_dir=str(MODELS_DIR / model_id),
    )

    api_key = _STUDIO_LLM_STATE.get("api_key") or secrets.token_urlsafe(18)
    _STUDIO_LLM_STATE["api_key"] = api_key

    ctx_size = spec["ctx_l4"] if stats["vram_total_gb"] >= 20.0 else spec["ctx_t4"]
    bin_dir = str(Path(llama_bin).parent)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"{bin_dir}:{env.get('LD_LIBRARY_PATH', '')}"

    _emit(
        "@@LLM_PROGRESS",
        {
            "stage": f"正在将 {model_id} 加载到 {stats['gpu_name']} 显存中（ctx={ctx_size}）...",
            "progress": 82,
            **stats,
        },
    )

    log_file = open("/content/llama_server.log", "w", encoding="utf-8")
    cmd = [
        llama_bin,
        "--model",
        gguf_path,
        "--mmproj",
        mmproj_path,
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
        "--api-key",
        api_key,
        "-ngl",
        "99",
        "--ctx-size",
        str(ctx_size),
        "--jinja",
        "--flash-attn",
        "on",
    ]
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)
    _STUDIO_LLM_STATE["llama_proc"] = proc

    # Wait until llama-server finishes loading weights into GPU VRAM
    ready = False
    deadline = time.time() + 180.0
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = Path("/content/llama_server.log").read_text(encoding="utf-8", errors="replace")[-800:]
            raise RuntimeError(f"llama-server 启动失败 (exit {proc.returncode}): {tail}")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(1.0)

    if not ready:
        tail = Path("/content/llama_server.log").read_text(encoding="utf-8", errors="replace")[-600:]
        raise RuntimeError(f"llama-server 加载超时：{tail}")

    public_url = _ensure_tunnel()
    _STUDIO_LLM_STATE["model_id"] = model_id
    _emit(
        "@@LLM_READY",
        {
            "ok": True,
            "url": public_url,
            "api_key": api_key,
            "model_id": model_id,
            **stats,
        },
    )


def _main():
    raw_b64 = os.environ.get("STUDIO_LLM_B64", "")
    payload = json.loads(base64.b64decode(raw_b64).decode("utf-8")) if raw_b64 else {}
    action = payload.get("action", "start")
    if action == "stop":
        _stop_llama_server()
        _emit("@@LLM_STOPPED", {"ok": True})
    elif action == "start":
        _start_llm(payload.get("model_id", "qwen3.8-27b"))
    else:
        raise ValueError(f"Unknown action: {action}")


try:
    _main()
except Exception as exc:
    _emit("@@LLM_ERROR", {"ok": False, "error": str(exc)})
    raise
