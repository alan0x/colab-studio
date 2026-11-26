"""
Agent Tool Suites for Colab Agent Studio:
1. Infinite Canvas Media Tools (generate_image, generate_video, list_canvas_items)
2. Web Research Tools (web_search, fetch_webpage)
3. Local Workspace File & Terminal Tools (list_files, read_file, write_file, run_command)
4. Google Drive Sync Status Tool (drive_status)
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

_SUBPROC_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

SENSITIVE_TOOLS = {"write_file", "run_command"}


def get_tool_schemas(enabled: dict[str, bool]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []

    if enabled.get("canvas", True):
        tools.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "generate_image",
                        "description": "在用户的无限画布上调用 Google Colab GPU 生成一张高清 AI 图片，并自动同步到 Google Drive。提示词建议使用高质量英文描述。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string", "description": "详细的画面英文提示词（包含主体、风格、光影、构图）"},
                                "model_id": {
                                    "type": "string",
                                    "enum": ["animagine-xl-4", "realvis-xl", "z-image-turbo", "shuttle-3.1", "sdxl-base"],
                                    "description": "动漫二次元选 animagine-xl-4；电影写实选 realvis-xl；中英双语极速选 z-image-turbo",
                                },
                                "ratio": {
                                    "type": "string",
                                    "enum": ["16:9", "9:16", "1:1", "4:3"],
                                    "description": "画面宽高比，默认 16:9",
                                },
                                "negative_prompt": {"type": "string", "description": "可选的反向提示词"},
                            },
                            "required": ["prompt"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "generate_video",
                        "description": "在无限画布上生成 AI 视频（支持文生视频或基于画布已有图片 ID 生成图生视频），自动连线并同步至 Google Drive。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string", "description": "视频画面与运镜英文提示词"},
                                "source_image_id": {"type": "string", "description": "可选：画布上某张参考图片的 ID（传入即为图生视频）"},
                                "model_id": {
                                    "type": "string",
                                    "enum": ["ltx-video", "wan2.2-5b", "wan2.1-1.3b"],
                                    "description": "默认 ltx-video（T4/L4极速支持图生视频）；追求高画质选 wan2.2-5b（需 L4/A100）",
                                },
                                "ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1", "4:3"]},
                                "num_frames": {"type": "integer", "description": "帧数：25(~1.5s) / 49(~3s) / 81(~5s)，默认 49"},
                            },
                            "required": ["prompt"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_canvas_items",
                        "description": "查看无限画布上现有的素材图、已生成图片和视频的列表及其 ID。",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ]
        )

    if enabled.get("web", True):
        tools.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "实时联网搜索网页信息，返回相关标题、链接和摘要。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "搜索关键词"},
                                "max_results": {"type": "integer", "description": "返回条数，默认 5"},
                            },
                            "required": ["query"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_webpage",
                        "description": "抓取并提取指定网页的可读正文内容（用于阅读文章、文档、GitHub 页面等）。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "完整的 http/https 网址"},
                                "max_chars": {"type": "integer", "description": "最大返回字符数，默认 6000"},
                            },
                            "required": ["url"],
                        },
                    },
                },
            ]
        )

    if enabled.get("local", True):
        tools.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "list_files",
                        "description": "列出本地工作区目录中的文件与子目录。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "相对于工作区的子路径，默认 '.'"},
                            },
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "读取本地工作区内的文本或代码文件内容。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "相对于工作区的文件路径"},
                                "start_line": {"type": "integer", "description": "起始行号（从 1 开始）"},
                                "end_line": {"type": "integer", "description": "结束行号，默认 200"},
                            },
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "description": "在本地工作区内创建或写入文件（会受安全确认策略保护）。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "相对于工作区的文件路径"},
                                "content": {"type": "string", "description": "要写入的完整文本内容"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "description": "在本地工作区目录中执行终端或 Python 命令（用于运行代码、测试或数据处理，受安全确认策略保护）。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {"type": "string", "description": "要执行的 shell 命令"},
                            },
                            "required": ["command"],
                        },
                    },
                },
            ]
        )

    return tools


def _safe_workspace_path(workspace_root: Path, rel_path: str) -> Path:
    root = workspace_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / (rel_path or ".")).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"安全限制：不允许访问工作区 ({root}) 之外的路径：{rel_path}")
    return target


def _strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def execute_web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    q = urllib.parse.urlencode({"q": query})
    url = f"https://html.duckduckgo.com/html/?{q}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        page = resp.read().decode("utf-8", errors="replace")

    results: list[dict[str, str]] = []
    # Parse DuckDuckGo HTML result blocks
    blocks = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.S)
    snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|<div[^>]*class="result__snippet"[^>]*>(.*?)</div>', page, flags=re.S)

    for idx, (href, raw_title) in enumerate(blocks[: max(1, min(10, max_results))]):
        real_url = href
        m_uddg = re.search(r"uddg=([^&]+)", href)
        if m_uddg:
            real_url = urllib.parse.unquote(m_uddg.group(1))
        title = _strip_html(raw_title)
        snip = ""
        if idx < len(snippets):
            snip = _strip_html(snippets[idx][0] or snippets[idx][1])
        results.append({"title": title, "url": real_url, "snippet": snip})

    return {"query": query, "count": len(results), "results": results}


def execute_fetch_webpage(url: str, max_chars: int = 6000) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise ValueError("仅支持 http:// 或 https:// 网址")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        },
    )
    with urllib.request.urlopen(req, timeout=18) as resp:
        raw = resp.read(2_000_000).decode("utf-8", errors="replace")
    text = _strip_html(raw)
    limit = max(500, min(20000, int(max_chars or 6000)))
    return {
        "url": url,
        "length": len(text),
        "truncated": len(text) > limit,
        "content": text[:limit],
    }


class ToolExecutor:
    def __init__(
        self,
        *,
        get_workspace_dir: Callable[[], Path],
        list_canvas_items: Callable[[], list[dict[str, Any]]],
        run_canvas_generation: Callable[[dict[str, Any], Optional[Callable[[dict[str, Any]], None]]], dict[str, Any]],
    ):
        self.get_workspace_dir = get_workspace_dir
        self.list_canvas_items = list_canvas_items
        self.run_canvas_generation = run_canvas_generation

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Any]:
        if name == "list_canvas_items":
            items = self.list_canvas_items()
            compact = [
                {
                    "id": it.get("id"),
                    "kind": it.get("kind"),
                    "model_id": it.get("model_id"),
                    "prompt": (it.get("prompt") or "")[:120],
                    "filename": it.get("filename"),
                    "local_url": it.get("local_url"),
                }
                for it in items[:25]
            ]
            return {"count": len(items), "items": compact}

        if name == "generate_image":
            ratio = args.get("ratio", "16:9")
            ratio_map = {"16:9": (1024, 576), "9:16": (576, 1024), "1:1": (1024, 1024), "4:3": (896, 672)}
            w, h = ratio_map.get(ratio, (1024, 576))
            model_id = args.get("model_id", "animagine-xl-4")
            step_defaults = {"animagine-xl-4": (28, 5.0), "realvis-xl": (25, 5.5), "z-image-turbo": (9, 0.0), "shuttle-3.1": (4, 0.0), "sdxl-base": (25, 7.5)}
            steps, guidance = step_defaults.get(model_id, (25, 5.5))
            params = {
                "task": "image",
                "model_id": model_id,
                "prompt": args.get("prompt", ""),
                "negative_prompt": args.get("negative_prompt", ""),
                "width": w,
                "height": h,
                "steps": steps,
                "guidance_scale": guidance,
                "seed": -1,
            }
            item = self.run_canvas_generation(
                params,
                lambda p: on_progress(p.get("stage", "正在渲染图片...")) if on_progress else None,
            )
            return {
                "ok": True,
                "media_item": item,
                "summary": f"已生成图片并添加到无限画布（ID: {item['id']}，模型: {model_id}，尺寸: {w}x{h}）",
            }

        if name == "generate_video":
            ratio = args.get("ratio", "16:9")
            ratio_map = {"16:9": (704, 480), "9:16": (480, 704), "1:1": (512, 512), "4:3": (640, 480)}
            w, h = ratio_map.get(ratio, (704, 480))
            model_id = args.get("model_id", "ltx-video")
            params = {
                "task": "video",
                "model_id": model_id,
                "prompt": args.get("prompt", ""),
                "parent_id": args.get("source_image_id") or None,
                "width": w,
                "height": h,
                "num_frames": int(args.get("num_frames") or 49),
                "fps": 24 if model_id == "ltx-video" else 16,
                "steps": 28 if model_id == "ltx-video" else 35,
                "guidance_scale": 3.5 if model_id == "ltx-video" else 5.0,
                "seed": -1,
            }
            item = self.run_canvas_generation(
                params,
                lambda p: on_progress(p.get("stage", "正在渲染视频...")) if on_progress else None,
            )
            return {
                "ok": True,
                "media_item": item,
                "summary": f"已生成视频并添加到无限画布（ID: {item['id']}，模型: {model_id}）",
            }

        if name == "web_search":
            return execute_web_search(args.get("query", ""), int(args.get("max_results") or 5))

        if name == "fetch_webpage":
            return execute_fetch_webpage(args.get("url", ""), int(args.get("max_chars") or 6000))

        if name == "list_files":
            ws = self.get_workspace_dir()
            target = _safe_workspace_path(ws, args.get("path", "."))
            if not target.exists():
                return {"error": f"路径不存在: {args.get('path', '.')}"}
            entries = []
            for p in sorted(target.iterdir())[:100]:
                entries.append(
                    {
                        "name": p.name,
                        "type": "dir" if p.is_dir() else "file",
                        "size": p.stat().st_size if p.is_file() else None,
                    }
                )
            return {"workspace": str(ws), "path": str(target.relative_to(ws.resolve())), "entries": entries}

        if name == "read_file":
            ws = self.get_workspace_dir()
            target = _safe_workspace_path(ws, args.get("path", ""))
            if not target.is_file():
                return {"error": f"文件不存在: {args.get('path')}"}
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            s = max(1, int(args.get("start_line") or 1))
            e = min(len(lines), int(args.get("end_line") or (s + 199)))
            numbered = "\n".join(f"{i}: {lines[i-1]}" for i in range(s, e + 1))
            return {"path": args.get("path"), "total_lines": len(lines), "range": f"{s}-{e}", "content": numbered[:16000]}

        if name == "write_file":
            ws = self.get_workspace_dir()
            target = _safe_workspace_path(ws, args.get("path", ""))
            target.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("content", ""))
            target.write_text(content, encoding="utf-8")
            return {"ok": True, "path": args.get("path"), "bytes_written": len(content.encode("utf-8"))}

        if name == "run_command":
            ws = self.get_workspace_dir()
            ws.mkdir(parents=True, exist_ok=True)
            cmd = str(args.get("command", "")).strip()
            if not cmd:
                return {"error": "命令不能为空"}
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(ws),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=_SUBPROC_FLAGS,
            )
            return {
                "command": cmd,
                "exit_code": proc.returncode,
                "stdout": (proc.stdout or "")[-6000:],
                "stderr": (proc.stderr or "")[-4000:],
            }

        return {"error": f"未知工具: {name}"}
