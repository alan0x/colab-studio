"""
Remote GPU Inference Worker — executed inside the persistent Google Colab Jupyter kernel
via `colab exec -s studio -f remote_worker.py`.

Because Colab CLI keeps the Jupyter kernel alive across `colab exec` calls, the loaded
diffusers pipeline stays cached in `_STUDIO_STATE` so repeated generations run immediately
without reloading weights!
"""

import base64
import gc
import inspect
import json
import os
import secrets
import time
import traceback

import torch
from PIL import Image

os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

# Persist loaded pipeline across `colab exec` invocations in the same kernel
if "_STUDIO_STATE" not in globals():
    _STUDIO_STATE = {"model_key": None, "pipe": None}


def _enable_vae_tiling(pipe) -> None:
    if hasattr(pipe, "vae") and pipe.vae is not None and hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()
    elif hasattr(pipe, "enable_vae_tiling"):
        pipe.enable_vae_tiling()


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


def _unload_pipe() -> None:
    if _STUDIO_STATE.get("pipe") is not None:
        try:
            del _STUDIO_STATE["pipe"]
        except Exception:
            pass
    _STUDIO_STATE["pipe"] = None
    _STUDIO_STATE["model_key"] = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def _align_dim(val: int, mult: int = 32, minimum: int = 256) -> int:
    return max(minimum, int(round(val / mult)) * mult)


def _align_frames(val: int, step: int = 8, minimum: int = 9) -> int:
    k = max(1, round((val - 1) / step))
    return max(minimum, k * step + 1)


def _get_pipeline(model_id: str, has_init_image: bool, vram_total_gb: float):
    cache_key = f"{model_id}:{'i2v' if has_init_image else 't2v'}"
    if _STUDIO_STATE.get("model_key") == cache_key and _STUDIO_STATE.get("pipe") is not None:
        return _STUDIO_STATE["pipe"]

    _unload_pipe()
    _emit(
        "@@PROGRESS",
        {
            "status": "loading_model",
            "stage": f"正在加载模型 {model_id}（首次开机需拉取权重，之后同会话秒开）...",
            "progress": 24,
            **_gpu_stats(),
        },
    )

    low_vram = vram_total_gb <= 18.5

    if model_id == "realvis-xl":
        from diffusers import StableDiffusionXLPipeline
        repo = "SG161222/RealVisXL_V5.0"
        try:
            pipe = StableDiffusionXLPipeline.from_pretrained(
                repo,
                torch_dtype=torch.float16,
                variant="fp16",
                use_safetensors=True,
            )
        except Exception:
            pipe = StableDiffusionXLPipeline.from_pretrained(
                repo,
                torch_dtype=torch.float16,
                use_safetensors=True,
            )
        pipe.enable_model_cpu_offload()
        _enable_vae_tiling(pipe)

    elif model_id == "z-image-turbo":
        from diffusers import ZImagePipeline
        pipe = ZImagePipeline.from_pretrained(
            "Tongyi-MAI/Z-Image-Turbo",
            torch_dtype=torch.bfloat16,
        )
        if low_vram:
            pipe.enable_sequential_cpu_offload()
        else:
            pipe.enable_model_cpu_offload()
        _enable_vae_tiling(pipe)

    elif model_id in ("shuttle-3.1", "flux-schnell"):
        from diffusers import FluxPipeline
        pipe = FluxPipeline.from_pretrained(
            "shuttleai/shuttle-3.1-aesthetic",
            torch_dtype=torch.bfloat16,
        )
        if low_vram:
            pipe.enable_sequential_cpu_offload()
        else:
            pipe.enable_model_cpu_offload()
        _enable_vae_tiling(pipe)

    elif model_id == "sdxl-base":
        from diffusers import StableDiffusionXLPipeline
        pipe = StableDiffusionXLPipeline.from_pretrained(
            "stabilityai/stable-diffusion-xl-base-1.0",
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
        )
        pipe.enable_model_cpu_offload()
        _enable_vae_tiling(pipe)

    elif model_id == "ltx-video":
        if has_init_image:
            from diffusers import LTXImageToVideoPipeline
            pipe = LTXImageToVideoPipeline.from_pretrained(
                "Lightricks/LTX-Video",
                torch_dtype=torch.bfloat16,
            )
        else:
            from diffusers import LTXVideoPipeline
            pipe = LTXVideoPipeline.from_pretrained(
                "Lightricks/LTX-Video",
                torch_dtype=torch.bfloat16,
            )
        if low_vram:
            pipe.enable_sequential_cpu_offload()
        else:
            pipe.enable_model_cpu_offload()
        _enable_vae_tiling(pipe)

    elif model_id == "wan2.1-1.3b":
        from diffusers import AutoencoderKLWan, WanPipeline
        repo = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
        vae = AutoencoderKLWan.from_pretrained(repo, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(repo, vae=vae, torch_dtype=torch.bfloat16)
        pipe.enable_model_cpu_offload()

    elif model_id == "wan2.2-5b":
        from diffusers import AutoencoderKLWan, WanPipeline
        repo = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
        vae = AutoencoderKLWan.from_pretrained(repo, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(repo, vae=vae, torch_dtype=torch.bfloat16)
        if vram_total_gb < 22:
            pipe.enable_sequential_cpu_offload()
        else:
            pipe.enable_model_cpu_offload()
    else:
        raise ValueError(f"不支持的模型: {model_id}")

    _STUDIO_STATE["model_key"] = cache_key
    _STUDIO_STATE["pipe"] = pipe
    return pipe


def _main():
    t0 = time.time()
    raw_b64 = os.environ.get("STUDIO_JOB_B64", "")
    if not raw_b64:
        raise RuntimeError("Missing STUDIO_JOB_B64 payload")
    job = json.loads(base64.b64decode(raw_b64).decode("utf-8"))

    stats = _gpu_stats()
    task = job.get("task", "image")
    model_id = job.get("model_id", "flux-schnell")
    prompt = job.get("prompt", "")
    negative_prompt = job.get("negative_prompt", "")
    width = _align_dim(int(job.get("width", 768)), 32)
    height = _align_dim(int(job.get("height", 512)), 32)
    total_steps = max(1, int(job.get("steps", 20)))
    guidance = float(job.get("guidance_scale", 3.5))
    seed_in = int(job.get("seed", -1))
    seed = seed_in if seed_in >= 0 else secrets.randbelow(2**31 - 1)
    remote_input_path = job.get("remote_input_path")
    remote_output_base = job.get("remote_output_base", "/content/studio_out")

    init_img = None
    if remote_input_path and os.path.exists(remote_input_path):
        init_img = Image.open(remote_input_path).convert("RGB")

    pipe = _get_pipeline(model_id, init_img is not None, stats["vram_total_gb"])
    gen = torch.Generator(device="cpu").manual_seed(seed)

    def step_callback(_pipe, step_idx, _timestep, callback_kwargs):
        cur = step_idx + 1
        pct = 30 + int((cur / total_steps) * 62)
        _emit(
            "@@PROGRESS",
            {
                "status": "running",
                "stage": f"{stats['gpu_name']} 渲染中 ({cur}/{total_steps} 步)...",
                "step": cur,
                "total_steps": total_steps,
                "progress": min(92, pct),
                "elapsed_seconds": round(time.time() - t0, 1),
                **_gpu_stats(),
            },
        )
        return callback_kwargs

    _emit(
        "@@PROGRESS",
        {
            "status": "running",
            "stage": f"开始渲染 ({width}×{height} · seed={seed})...",
            "step": 0,
            "total_steps": total_steps,
            "progress": 30,
            "elapsed_seconds": round(time.time() - t0, 1),
            **_gpu_stats(),
        },
    )

    if task == "image":
        call_params = inspect.signature(pipe.__call__).parameters
        kwargs = dict(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=total_steps,
            guidance_scale=guidance,
            generator=gen,
        )
        if "callback_on_step_end" in call_params:
            kwargs["callback_on_step_end"] = step_callback
        if model_id in ("realvis-xl", "sdxl-base") and negative_prompt and "negative_prompt" in call_params:
            kwargs["negative_prompt"] = negative_prompt
        image = pipe(**kwargs).images[0]
        out_path = f"{remote_output_base}.png"
        image.save(out_path)
        ext = "png"
    else:
        from diffusers.utils import export_to_video

        step_mult = 8 if model_id == "ltx-video" else 4
        num_frames = _align_frames(int(job.get("num_frames", 49)), step=step_mult, minimum=9)
        fps = int(job.get("fps", 24 if model_id == "ltx-video" else 16))
        if init_img is not None:
            init_img = init_img.resize((width, height), Image.LANCZOS)

        call_params = inspect.signature(pipe.__call__).parameters
        kwargs = dict(
            prompt=prompt,
            width=width,
            height=height,
            num_frames=num_frames,
            num_inference_steps=total_steps,
            guidance_scale=guidance,
            generator=gen,
        )
        if "negative_prompt" in call_params and negative_prompt:
            kwargs["negative_prompt"] = negative_prompt
        if "callback_on_step_end" in call_params:
            kwargs["callback_on_step_end"] = step_callback
        if init_img is not None:
            if "image" in call_params:
                kwargs["image"] = init_img
            elif model_id == "wan2.2-5b":
                from diffusers import WanImageToVideoPipeline
                pipe = WanImageToVideoPipeline.from_pipe(pipe)
                kwargs["image"] = init_img

        frames = pipe(**kwargs).frames[0]
        _emit(
            "@@PROGRESS",
            {
                "status": "encoding",
                "stage": "正在封装高清 MP4 视频...",
                "progress": 94,
                "elapsed_seconds": round(time.time() - t0, 1),
                **_gpu_stats(),
            },
        )
        out_path = f"{remote_output_base}.mp4"
        export_to_video(frames, out_path, fps=fps)
        ext = "mp4"

    final_stats = _gpu_stats()
    _emit(
        "@@RESULT",
        {
            "ok": True,
            "remote_output": out_path,
            "file_ext": ext,
            "seed": seed,
            "width": width,
            "height": height,
            "elapsed_seconds": round(time.time() - t0, 1),
            **final_stats,
        },
    )


try:
    _main()
except Exception as exc:
    traceback.print_exc()
    _emit("@@RESULT", {"ok": False, "error": str(exc)})
    raise
finally:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
