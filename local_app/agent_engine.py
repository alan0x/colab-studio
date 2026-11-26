"""
Multimodal ReAct Agent Engine & Chat Session Store.

Talks directly to the Colab-hosted `llama-server` OpenAI-compatible API
(`/v1/chat/completions`, stream=true) with:
- Native multimodal image & document attachments
- Streaming Thinking (`reasoning_content` AND inline `<think>...</think>`)
- Multi-step Tool Calling (Canvas image/video generation, Web search/fetch,
  Local workspace file/terminal tools) with optional inline User Approval Gate
- Persistent multi-session chat history (locally in `chats/sessions.json` + Drive sync)
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from agent_tools import SENSITIVE_TOOLS, ToolExecutor, get_tool_schemas

SYSTEM_PROMPT = """你是 Colab Agent Studio 的全能多模态 AI 助手（类似 ChatGPT / Claude / Antigravity），运行在用户专属的 Google Colab 云端 GPU 上。
你具备以下真实工具能力（按当前开启的开关自动提供）：
1. 无限画布创作（generate_image / generate_video / list_canvas_items）：可以根据用户需求先构思高质量英文画面/运镜提示词，再调用工具在无限画布中生成图片或图生视频。
2. 联网检索（web_search / fetch_webpage）：需要实时信息、最新技术文档或新闻时主动联网查证。
3. 本地工作区与终端（list_files / read_file / write_file / run_command）：像 Claude Code / Antigravity 一样读写代码、运行脚本和分析本地文件。

请用简洁、清晰、专业的中文（或跟随用户语言）回复，善用 Markdown 排版。调用工具获得结果后，请整合结果清晰呈现给用户。"""


class AgentEngine:
    def __init__(
        self,
        *,
        data_dir: Path,
        colab_manager: Any,
        tool_executor: ToolExecutor,
        get_settings: Callable[[], dict[str, Any]],
    ):
        self.chats_dir = data_dir / "chats"
        self.chats_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_path = self.chats_dir / "sessions.json"
        self.colab = colab_manager
        self.tools = tool_executor
        self.get_settings = get_settings

        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, dict[str, Any]] = {}

    # --------------------------------------------------------
    # Persistent Chat Sessions
    # --------------------------------------------------------
    def list_sessions(self) -> list[dict[str, Any]]:
        if not self.sessions_path.exists():
            return []
        try:
            data = json.loads(self.sessions_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def _save_sessions(self, sessions: list[dict[str, Any]]) -> None:
        self.sessions_path.write_text(json.dumps(sessions, indent=2, ensure_ascii=False), encoding="utf-8")

    def create_session(self, title: str = "新对话", model_id: str = "qwen3.8-27b") -> dict[str, Any]:
        sess = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "model_id": model_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "messages": [],
        }
        with self._lock:
            sessions = self.list_sessions()
            sessions.insert(0, sess)
            self._save_sessions(sessions)
        return sess

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        for s in self.list_sessions():
            if s.get("id") == session_id:
                return s
        return None

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            sessions = self.list_sessions()
            rem = [s for s in sessions if s.get("id") != session_id]
            if len(rem) == len(sessions):
                return False
            self._save_sessions(rem)
            return True

    def _update_session_messages(self, session_id: str, messages: list[dict[str, Any]], title_hint: Optional[str] = None) -> None:
        with self._lock:
            sessions = self.list_sessions()
            for s in sessions:
                if s.get("id") == session_id:
                    s["messages"] = messages
                    s["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    if title_hint and (s.get("title") in ("新对话", "", None)):
                        s["title"] = title_hint[:28]
                    break
            self._save_sessions(sessions)

    # --------------------------------------------------------
    # Streaming Agent Run Lifecycle
    # --------------------------------------------------------
    def start_run(
        self,
        *,
        session_id: Optional[str],
        user_text: str,
        attachments: list[dict[str, Any]],
        model_id: str,
        thinking: bool,
        enabled_tools: dict[str, bool],
        require_approval: bool,
    ) -> dict[str, Any]:
        sess = self.get_session(session_id) if session_id else None
        if not sess:
            sess = self.create_session(title=user_text.strip()[:28] or "新对话", model_id=model_id)
        sid = sess["id"]

        user_msg = {
            "id": uuid.uuid4().hex[:10],
            "role": "user",
            "content": user_text,
            "attachments": attachments,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        msgs = list(sess.get("messages", []))
        msgs.append(user_msg)
        self._update_session_messages(sid, msgs, title_hint=user_text.strip())

        run_id = uuid.uuid4().hex[:12]
        run_obj = {
            "run_id": run_id,
            "session_id": sid,
            "status": "running",
            "cancel_requested": False,
            "events": [],
            "cond": threading.Condition(),
        }
        with self._lock:
            self._runs[run_id] = run_obj

        threading.Thread(
            target=self._execute_agent_loop,
            kwargs=dict(
                run_id=run_id,
                session_id=sid,
                history_messages=msgs,
                model_id=model_id,
                thinking=thinking,
                enabled_tools=enabled_tools,
                require_approval=require_approval,
            ),
            daemon=True,
        ).start()
        return {"run_id": run_id, "session_id": sid, "user_message": user_msg}

    def _emit_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        run = self._runs.get(run_id)
        if not run:
            return
        with run["cond"]:
            run["events"].append({"type": event_type, "data": data, "ts": time.time()})
            run["cond"].notify_all()

    def iter_events(self, run_id: str, cursor: int = 0):
        run = self._runs.get(run_id)
        if not run:
            yield "error", {"error": "Run not found"}
            return
        idx = cursor
        while True:
            with run["cond"]:
                while idx >= len(run["events"]) and run["status"] == "running":
                    run["cond"].wait(timeout=12.0)
                    if idx >= len(run["events"]) and run["status"] == "running":
                        yield "ping", {}
                batch = run["events"][idx:]
                idx += len(batch)
                done = run["status"] != "running" and idx >= len(run["events"])
            for ev in batch:
                yield ev["type"], ev["data"]
            if done:
                break

    def cancel_run(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if not run:
            return False
        run["cancel_requested"] = True
        # Also release any waiting approval
        for ap in list(self._approvals.values()):
            if ap.get("run_id") == run_id:
                ap["approved"] = False
                ap["event"].set()
        self._emit_event(run_id, "status", {"stage": "正在停止生成..."})
        return True

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        ap = self._approvals.get(approval_id)
        if not ap:
            return False
        ap["approved"] = bool(approved)
        ap["event"].set()
        return True

    # --------------------------------------------------------
    # Core OpenAI-Compatible Streaming & ReAct Loop
    # --------------------------------------------------------
    def _build_api_messages(self, history_messages: list[dict[str, Any]], thinking: bool) -> list[dict[str, Any]]:
        sys_text = SYSTEM_PROMPT
        if not thinking:
            sys_text += "\n请直接给出简洁答案，不要输出冗长的思考过程。"
        api_msgs: list[dict[str, Any]] = [{"role": "system", "content": sys_text}]

        for m in history_messages[-16:]:
            role = m.get("role")
            if role == "user":
                atts = m.get("attachments") or []
                text_parts = []
                img_parts = []
                for att in atts:
                    kind = att.get("kind")
                    if kind == "image" and att.get("data_url"):
                        img_parts.append({"type": "image_url", "image_url": {"url": att["data_url"]}})
                    elif kind == "text" and att.get("text_content"):
                        text_parts.append(f"\n[附件文件: {att.get('name', 'file')}]\n```\n{att['text_content'][:12000]}\n```")
                full_text = (m.get("content") or "") + "".join(text_parts)
                if img_parts:
                    content: Any = [{"type": "text", "text": full_text or "请分析这张图片"}] + img_parts
                else:
                    content = full_text
                api_msgs.append({"role": "user", "content": content})
            elif role == "assistant":
                api_msgs.append({"role": "assistant", "content": m.get("content") or ""})
        return api_msgs

    def _stream_completion(
        self,
        *,
        run_id: str,
        endpoint_url: str,
        api_key: str,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        thinking: bool,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        """Streams one completion turn from Colab's llama-server."""
        url = endpoint_url.rstrip("/") + "/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "stream": True,
            "temperature": 0.6 if thinking else 0.7,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        run = self._runs[run_id]
        content_acc = ""
        thinking_acc = ""
        in_think_tag = False
        pending_buf = ""
        tool_calls_by_idx: dict[int, dict[str, Any]] = {}

        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw_line in resp:
                if run["cancel_requested"]:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                # 1. Native reasoning_content field
                rc = delta.get("reasoning_content")
                if rc:
                    thinking_acc += rc
                    self._emit_event(run_id, "thinking", {"delta": rc})

                # 2. Content tokens (with inline <think>...</think> detection)
                c = delta.get("content")
                if c:
                    pending_buf += c
                    while pending_buf:
                        if not in_think_tag:
                            idx_open = pending_buf.find("<think>")
                            if idx_open != -1:
                                before = pending_buf[:idx_open]
                                if before:
                                    content_acc += before
                                    self._emit_event(run_id, "delta", {"delta": before})
                                pending_buf = pending_buf[idx_open + 7 :]
                                in_think_tag = True
                            elif pending_buf.endswith(("<", "<t", "<th", "<thi", "<thin", "<think")):
                                break
                            else:
                                content_acc += pending_buf
                                self._emit_event(run_id, "delta", {"delta": pending_buf})
                                pending_buf = ""
                        else:
                            idx_close = pending_buf.find("</think>")
                            if idx_close != -1:
                                thought = pending_buf[:idx_close]
                                if thought:
                                    thinking_acc += thought
                                    self._emit_event(run_id, "thinking", {"delta": thought})
                                pending_buf = pending_buf[idx_close + 8 :]
                                in_think_tag = False
                            elif pending_buf.endswith(("<", "</", "</t", "</th", "</thi", "</thin", "</think")):
                                break
                            else:
                                thinking_acc += pending_buf
                                self._emit_event(run_id, "thinking", {"delta": pending_buf})
                                pending_buf = ""

                # 3. Native OpenAI tool_calls chunks
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index", 0))
                    entry = tool_calls_by_idx.setdefault(
                        idx,
                        {"id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        entry["function"]["arguments"] += fn["arguments"]

        # Flush remaining buffer
        if pending_buf:
            if in_think_tag:
                thinking_acc += pending_buf
                self._emit_event(run_id, "thinking", {"delta": pending_buf})
            else:
                content_acc += pending_buf
                self._emit_event(run_id, "delta", {"delta": pending_buf})

        tool_calls = [tool_calls_by_idx[k] for k in sorted(tool_calls_by_idx)]

        # Fallback: parse inline <tool_call>{"name":..., "arguments":...}</tool_call> emitted by Qwen
        if not tool_calls and "<tool_call>" in content_acc:
            matches = re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content_acc, flags=re.S)
            for raw_json in matches:
                try:
                    parsed = json.loads(raw_json)
                    if parsed.get("name"):
                        tool_calls.append(
                            {
                                "id": f"call_{uuid.uuid4().hex[:8]}",
                                "type": "function",
                                "function": {
                                    "name": parsed["name"],
                                    "arguments": json.dumps(parsed.get("arguments", {}), ensure_ascii=False),
                                },
                            }
                        )
                except Exception:
                    pass
            if tool_calls:
                content_acc = re.sub(r"<tool_call>.*?</tool_call>", "", content_acc, flags=re.S).strip()

        return content_acc, thinking_acc, tool_calls

    def _execute_agent_loop(
        self,
        *,
        run_id: str,
        session_id: str,
        history_messages: list[dict[str, Any]],
        model_id: str,
        thinking: bool,
        enabled_tools: dict[str, bool],
        require_approval: bool,
    ) -> None:
        run = self._runs[run_id]
        all_thinking = ""
        final_content = ""
        tool_traces: list[dict[str, Any]] = []
        media_items: list[dict[str, Any]] = []

        try:
            self._emit_event(run_id, "status", {"stage": f"正在准备 Colab 云端多模态大脑 ({model_id})...", "progress": 8})
            llm_info = self.colab.ensure_llm_server(
                model_id=model_id,
                on_progress=lambda stage, pct: self._emit_event(run_id, "status", {"stage": stage, "progress": pct}),
            )
            self._emit_event(run_id, "status", {"stage": "模型已就绪，正在思考回复...", "progress": 100})

            api_msgs = self._build_api_messages(history_messages, thinking=thinking)
            schemas = get_tool_schemas(enabled_tools)

            max_turns = 6
            for turn in range(max_turns):
                if run["cancel_requested"]:
                    break

                # If a media tool swapped VRAM on the previous turn, seamlessly re-warm LLM from cached GGUF!
                if not self.colab.llm.get("ready"):
                    self._emit_event(run_id, "status", {"stage": "画布渲染完成，正在从缓存秒速恢复对话模型...", "progress": 50})
                    llm_info = self.colab.ensure_llm_server(
                        model_id=model_id,
                        on_progress=lambda stage, pct: self._emit_event(run_id, "status", {"stage": stage, "progress": pct}),
                    )

                self.colab.touch_activity()
                turn_content, turn_think, tool_calls = self._stream_completion(
                    run_id=run_id,
                    endpoint_url=llm_info["url"],
                    api_key=llm_info["api_key"],
                    model_id=model_id,
                    messages=api_msgs,
                    tools=schemas,
                    thinking=thinking,
                )
                if turn_think:
                    all_thinking += ("\n" if all_thinking else "") + turn_think
                if turn_content:
                    final_content += ("\n\n" if final_content and turn > 0 else "") + turn_content

                if not tool_calls or run["cancel_requested"]:
                    break

                # Record assistant tool_calls turn in API messages
                api_msgs.append(
                    {
                        "role": "assistant",
                        "content": turn_content or "",
                        "tool_calls": tool_calls,
                    }
                )

                for tc in tool_calls:
                    if run["cancel_requested"]:
                        break
                    call_id = tc["id"]
                    fn_name = tc["function"]["name"]
                    raw_args = tc["function"].get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except Exception:
                        parsed_args = {}

                    self._emit_event(
                        run_id,
                        "tool_call",
                        {"call_id": call_id, "name": fn_name, "arguments": parsed_args},
                    )

                    # Approval gate for sensitive local write/command tools
                    approved = True
                    if require_approval and fn_name in SENSITIVE_TOOLS:
                        ap_id = uuid.uuid4().hex[:10]
                        ap_ev = threading.Event()
                        self._approvals[ap_id] = {"run_id": run_id, "event": ap_ev, "approved": False}
                        self._emit_event(
                            run_id,
                            "approval_required",
                            {"approval_id": ap_id, "call_id": call_id, "name": fn_name, "arguments": parsed_args},
                        )
                        ap_ev.wait(timeout=300)
                        approved = bool(self._approvals.pop(ap_id, {}).get("approved", False))

                    if not approved:
                        result_obj: dict[str, Any] = {"ok": False, "error": "用户拒绝了此操作"}
                    else:
                        try:
                            result_obj = self.tools.execute(
                                fn_name,
                                parsed_args,
                                on_progress=lambda st: self._emit_event(run_id, "status", {"stage": st}),
                            )
                        except Exception as te:
                            result_obj = {"ok": False, "error": str(te)}

                    if isinstance(result_obj, dict) and result_obj.get("media_item"):
                        media_items.append(result_obj["media_item"])

                    trace_item = {
                        "call_id": call_id,
                        "name": fn_name,
                        "arguments": parsed_args,
                        "result": result_obj,
                    }
                    tool_traces.append(trace_item)
                    self._emit_event(run_id, "tool_result", trace_item)

                    api_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": fn_name,
                            "content": json.dumps(result_obj, ensure_ascii=False)[:10000],
                        }
                    )

            assistant_msg = {
                "id": uuid.uuid4().hex[:10],
                "role": "assistant",
                "model_id": model_id,
                "content": final_content.strip(),
                "thinking": all_thinking.strip(),
                "tools": tool_traces,
                "media_items": media_items,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            updated = list(history_messages) + [assistant_msg]
            self._update_session_messages(session_id, updated)

            with run["cond"]:
                run["status"] = "completed"
                run["events"].append({"type": "done", "data": {"message": assistant_msg, "session_id": session_id}, "ts": time.time()})
                run["cond"].notify_all()
        except Exception as e:
            with run["cond"]:
                run["status"] = "failed"
                run["events"].append({"type": "error", "data": {"error": str(e)}, "ts": time.time()})
                run["cond"].notify_all()
