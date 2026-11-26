(() => {
  const $ = (id) => document.getElementById(id);

  const agentState = {
    sessions: [],
    currentSessionId: null,
    messages: [],
    attachments: [], // { id, kind: 'image'|'text', name, data_url?, text_content? }
    activeRunId: null,
    eventSource: null,
    streamingMsgEl: null,
    streamingContent: "",
    streamingThinking: "",
  };

  function toast(msg, type = "info") {
    if (window.StudioBridge?.toast) return window.StudioBridge.toast(msg, type);
    console.log(msg);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
    return data;
  }

  // ==================== LIGHTWEIGHT SAFE MARKDOWN RENDERER ====================
  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderMarkdown(md) {
    if (!md) return "";
    const codeBlocks = [];
    let text = String(md).replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      const idx = codeBlocks.length;
      const safeCode = escapeHtml(code.replace(/\n$/, ""));
      codeBlocks.push(
        `<div class="md-code-block"><div class="md-code-head"><span>${escapeHtml(lang || "code")}</span><button class="md-copy-btn" data-copy-idx="${idx}">复制</button></div><pre><code>${safeCode}</code></pre></div>`
      );
      return `@@CODEBLOCK_${idx}@@`;
    });

    text = escapeHtml(text);
    // Inline code
    text = text.replace(/`([^`\n]+)`/g, '<code class="md-inline">$1</code>');
    // Headings
    text = text.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    text = text.replace(/^## (.+)$/gm, "<h3>$1</h3>");
    text = text.replace(/^# (.+)$/gm, "<h2>$1</h2>");
    // Bold & Italic
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    // Links
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // Bullet lines
    text = text.replace(/^[\-\*] (.+)$/gm, "<li>$1</li>");
    text = text.replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`);
    // Line breaks
    text = text.replace(/\n\n+/g, "</p><p>");
    text = `<p>${text.replace(/\n/g, "<br/>")}</p>`;
    text = text.replace(/<p>\s*<\/p>/g, "");

    for (let i = 0; i < codeBlocks.length; i++) {
      text = text.replace(`@@CODEBLOCK_${i}@@`, codeBlocks[i]);
    }
    return text;
  }

  function wireCopyButtons(container) {
    container.querySelectorAll(".md-copy-btn").forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", () => {
        const pre = btn.closest(".md-code-block")?.querySelector("code");
        if (pre) {
          navigator.clipboard?.writeText(pre.textContent || "");
          btn.textContent = "已复制";
          setTimeout(() => (btn.textContent = "复制"), 1400);
        }
      });
    });
  }

  // ==================== WORKSPACE VIEW SWITCHER (AGENT <-> INFINITE CANVAS) ====================
  function switchWorkspaceView(view) {
    const isAgent = view === "agent";
    $("switchAgentBtn").classList.toggle("active", isAgent);
    $("switchCanvasBtn").classList.toggle("active", !isAgent);
    $("agentWorkspace").classList.toggle("hidden", !isAgent);
    $("canvasViewport").classList.toggle("hidden", isAgent);
    $("canvasToolRail").classList.toggle("hidden", isAgent);
    $("canvasZoomPill").classList.toggle("hidden", isAgent);
    $("canvasComposerWrap").classList.toggle("hidden", isAgent);

    if (!isAgent && window.StudioBridge?.refreshCanvas) {
      window.StudioBridge.refreshCanvas();
    }
  }

  $("switchAgentBtn").addEventListener("click", () => switchWorkspaceView("agent"));
  $("switchCanvasBtn").addEventListener("click", () => switchWorkspaceView("canvas"));

  // ==================== SESSION LIST & RENDERING ====================
  async function loadSessions() {
    try {
      const data = await api("/api/agent/sessions");
      agentState.sessions = data.sessions || [];
      const st = data.settings || {};
      if (st.agent_model) $("agentModelSelect").value = st.agent_model;
      if (st.agent_workspace) $("agentWorkspaceInput").value = st.agent_workspace;
      if (st.agent_require_approval != null) $("chkRequireApproval").checked = Boolean(st.agent_require_approval);
      renderSessionList();
    } catch (_) {}
  }

  function renderSessionList() {
    const list = $("chatSessionList");
    if (!agentState.sessions.length) {
      list.innerHTML = `<div class="chat-list-empty">暂无历史对话</div>`;
      return;
    }
    list.innerHTML = "";
    for (const s of agentState.sessions) {
      const el = document.createElement("div");
      el.className = `chat-list-item ${s.id === agentState.currentSessionId ? "active" : ""}`;
      el.innerHTML = `
        <span class="cli-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title || "新对话")}</span>
        <button class="cli-del" title="删除对话">✕</button>
      `;
      el.addEventListener("click", () => openSession(s.id));
      el.querySelector(".cli-del").addEventListener("click", async (e) => {
        e.stopPropagation();
        await api(`/api/agent/sessions/${s.id}`, { method: "DELETE" });
        if (agentState.currentSessionId === s.id) {
          agentState.currentSessionId = null;
          agentState.messages = [];
          renderMessages();
        }
        loadSessions();
      });
      list.appendChild(el);
    }
  }

  async function openSession(sid) {
    try {
      const data = await api(`/api/agent/sessions/${sid}`);
      agentState.currentSessionId = sid;
      agentState.messages = data.session?.messages || [];
      renderSessionList();
      renderMessages();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  $("newChatBtn").addEventListener("click", () => {
    agentState.currentSessionId = null;
    agentState.messages = [];
    renderSessionList();
    renderMessages();
    $("chatInput").focus();
  });

  // Settings persistence
  $("agentModelSelect").addEventListener("change", () => {
    api("/api/settings", { method: "POST", body: JSON.stringify({ agent_model: $("agentModelSelect").value }) }).catch(() => {});
  });
  $("agentWorkspaceInput").addEventListener("change", () => {
    api("/api/settings", { method: "POST", body: JSON.stringify({ agent_workspace: $("agentWorkspaceInput").value.trim() }) }).then(() => {
      toast("已更新 Agent 本地工作区路径", "success");
    });
  });
  $("chkRequireApproval").addEventListener("change", () => {
    api("/api/settings", { method: "POST", body: JSON.stringify({ agent_require_approval: $("chkRequireApproval").checked }) }).catch(() => {});
  });

  // ==================== MESSAGE RENDERING ====================
  function scrollToBottom() {
    const sc = $("chatScroll");
    sc.scrollTop = sc.scrollHeight;
  }

  function toolLabel(name) {
    const map = {
      generate_image: "🎨 调用无限画布生图",
      generate_video: "🎬 调用无限画布生视频",
      list_canvas_items: "🗂️ 读取无限画布作品",
      web_search: "🔍 实时联网搜索",
      fetch_webpage: "🌐 抓取网页正文",
      list_files: "📁 列出本地目录",
      read_file: "📄 读取本地文件",
      write_file: "✏️ 写入本地文件",
      run_command: "💻 执行本地终端命令",
    };
    return map[name] || `🔧 调用工具: ${name}`;
  }

  function buildToolCardHtml(tc) {
    const argsStr = escapeHtml(JSON.stringify(tc.arguments || {}, null, 2));
    const resObj = tc.result;
    const isDone = resObj !== undefined;
    const isErr = isDone && resObj && resObj.ok === false;
    const mediaItem = resObj?.media_item;

    let mediaHtml = "";
    if (mediaItem) {
      const isVid = mediaItem.kind === "video" || String(mediaItem.filename || "").endsWith(".mp4");
      mediaHtml = `
        <div class="chat-media-card">
          ${isVid ? `<video src="${mediaItem.local_url}" controls loop playsinline></video>` : `<img src="${mediaItem.local_url}" alt="Generated media" />`}
          <div class="chat-media-bar">
            <span>${escapeHtml(mediaItem.model_id || "")}</span>
            <button class="nt-btn accent jump-canvas-btn" data-item-id="${escapeHtml(mediaItem.id)}">在无限画布中查看 ↗</button>
          </div>
        </div>`;
    }

    const resPreview = isDone
      ? escapeHtml(JSON.stringify(resObj, null, 2)).slice(0, 1200)
      : "正在执行…";

    return `
      <div class="tool-card ${isDone ? (isErr ? "err" : "done") : "running"}" data-call-id="${escapeHtml(tc.call_id || "")}">
        <details>
          <summary>
            <span class="tc-status">${isDone ? (isErr ? "⚠️" : "✅") : "⏳"}</span>
            <span class="tc-title">${toolLabel(tc.name)}</span>
          </summary>
          <div class="tc-body">
            <div class="tc-sec"><strong>参数</strong><pre>${argsStr}</pre></div>
            <div class="tc-sec"><strong>返回</strong><pre>${resPreview}</pre></div>
          </div>
        </details>
        ${mediaHtml}
      </div>`;
  }

  function wireMediaJumpButtons(container) {
    container.querySelectorAll(".jump-canvas-btn").forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", () => {
        switchWorkspaceView("canvas");
        window.StudioBridge?.focusItem?.(btn.dataset.itemId);
      });
    });
  }

  function renderMessageElement(msg) {
    const wrap = document.createElement("div");
    wrap.className = `msg-row ${msg.role}`;

    if (msg.role === "user") {
      const attHtml = (msg.attachments || [])
        .map((a) =>
          a.kind === "image"
            ? `<img class="msg-att-img" src="${a.data_url}" alt="${escapeHtml(a.name)}" />`
            : `<span class="msg-att-file">📄 ${escapeHtml(a.name)}</span>`
        )
        .join("");
      wrap.innerHTML = `
        <div class="msg-bubble user-bubble">
          ${attHtml ? `<div class="msg-att-row">${attHtml}</div>` : ""}
          <div class="msg-text">${escapeHtml(msg.content || "").replace(/\n/g, "<br/>")}</div>
        </div>`;
      return wrap;
    }

    const thinkHtml = msg.thinking
      ? `<details class="think-box"><summary>🧠 深度思考过程</summary><div class="think-body">${escapeHtml(msg.thinking).replace(/\n/g, "<br/>")}</div></details>`
      : "";
    const toolsHtml = (msg.tools || []).map(buildToolCardHtml).join("");
    const bodyHtml = renderMarkdown(msg.content || "");

    wrap.innerHTML = `
      <div class="msg-avatar">✦</div>
      <div class="msg-assistant-col">
        <div class="msg-model-tag">${escapeHtml(msg.model_id || "Colab Agent")}</div>
        ${thinkHtml}
        <div class="msg-tools-wrap">${toolsHtml}</div>
        <div class="msg-md">${bodyHtml}</div>
      </div>`;
    wireCopyButtons(wrap);
    wireMediaJumpButtons(wrap);
    return wrap;
  }

  function renderMessages() {
    const box = $("chatMessages");
    box.innerHTML = "";
    $("agentWelcome").classList.toggle("hidden", agentState.messages.length > 0);
    for (const m of agentState.messages) {
      box.appendChild(renderMessageElement(m));
    }
    scrollToBottom();
  }

  // ==================== ATTACHMENTS (IMAGES & CODE/DOCS) ====================
  async function addAttachmentFiles(files) {
    for (const file of files) {
      if (file.type.startsWith("image/")) {
        const dataUrl = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(String(r.result || ""));
          r.onerror = rej;
          r.readAsDataURL(file);
        });
        agentState.attachments.push({ id: Math.random().toString(36).slice(2), kind: "image", name: file.name, data_url: dataUrl });
      } else {
        const txt = await file.text();
        agentState.attachments.push({ id: Math.random().toString(36).slice(2), kind: "text", name: file.name, text_content: txt.slice(0, 15000) });
      }
    }
    renderAttachments();
  }

  function renderAttachments() {
    const bar = $("chatAttachments");
    if (!agentState.attachments.length) {
      bar.classList.add("hidden");
      bar.innerHTML = "";
      return;
    }
    bar.classList.remove("hidden");
    bar.innerHTML = "";
    for (const att of agentState.attachments) {
      const chip = document.createElement("div");
      chip.className = "att-chip";
      chip.innerHTML =
        att.kind === "image"
          ? `<img src="${att.data_url}" /><span>${escapeHtml(att.name)}</span><button>✕</button>`
          : `<span>📄 ${escapeHtml(att.name)}</span><button>✕</button>`;
      chip.querySelector("button").addEventListener("click", () => {
        agentState.attachments = agentState.attachments.filter((x) => x.id !== att.id);
        renderAttachments();
      });
      bar.appendChild(chip);
    }
  }

  $("chatAttachBtn").addEventListener("click", () => $("chatFileInput").click());
  $("starterAttachImgBtn").addEventListener("click", () => $("chatFileInput").click());
  $("chatFileInput").addEventListener("change", () => {
    if ($("chatFileInput").files?.length) addAttachmentFiles(Array.from($("chatFileInput").files));
    $("chatFileInput").value = "";
  });

  // Starter cards
  document.querySelectorAll(".starter-card[data-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("chatInput").value = btn.dataset.prompt;
      $("chatInput").focus();
    });
  });

  // ==================== STREAMING CHAT EXECUTION ====================
  function setSendingState(active) {
    const btn = $("chatSendBtn");
    if (active) {
      btn.classList.add("stop-mode");
      $("chatSendText").textContent = "⏹ 停止";
    } else {
      btn.classList.remove("stop-mode");
      $("chatSendText").textContent = "发送";
    }
  }

  async function sendChatMessage() {
    if (agentState.activeRunId) {
      // Stop current streaming run
      const rid = agentState.activeRunId;
      agentState.eventSource?.close();
      agentState.eventSource = null;
      agentState.activeRunId = null;
      setSendingState(false);
      await api(`/api/agent/runs/${rid}/stop`, { method: "POST" }).catch(() => {});
      toast("已停止当前回复", "success");
      return;
    }

    const text = $("chatInput").value.trim();
    if (!text && !agentState.attachments.length) return;

    const payload = {
      session_id: agentState.currentSessionId,
      message: text,
      attachments: agentState.attachments,
      model_id: $("agentModelSelect").value,
      thinking: $("chkThinking").checked,
      enabled_tools: {
        canvas: $("chkToolCanvas").checked,
        web: $("chkToolWeb").checked,
        local: $("chkToolLocal").checked,
      },
      require_approval: $("chkRequireApproval").checked,
    };

    try {
      setSendingState(true);
      const res = await api("/api/agent/chat", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      agentState.currentSessionId = res.session_id;
      agentState.activeRunId = res.run_id;
      agentState.messages.push(res.user_message);
      agentState.attachments = [];
      $("chatInput").value = "";
      renderAttachments();
      renderMessages();
      loadSessions();

      // Create live streaming assistant container
      const box = $("chatMessages");
      const streamRow = document.createElement("div");
      streamRow.className = "msg-row assistant streaming";
      streamRow.innerHTML = `
        <div class="msg-avatar">✦</div>
        <div class="msg-assistant-col">
          <div class="msg-model-tag">${escapeHtml(payload.model_id)}</div>
          <div class="agent-live-status" id="liveAgentStatus">正在连接 Colab 云端大模型…</div>
          <details class="think-box hidden" id="liveThinkBox" open><summary>🧠 正在深度思考…</summary><div class="think-body" id="liveThinkBody"></div></details>
          <div class="msg-tools-wrap" id="liveToolsWrap"></div>
          <div class="msg-md" id="liveMdBody"></div>
        </div>`;
      box.appendChild(streamRow);
      scrollToBottom();

      let accThink = "";
      let accContent = "";

      const es = new EventSource(`/api/agent/stream/${res.run_id}`);
      agentState.eventSource = es;

      es.addEventListener("status", (ev) => {
        const d = JSON.parse(ev.data || "{}");
        const stEl = streamRow.querySelector("#liveAgentStatus");
        if (stEl) {
          stEl.classList.remove("hidden");
          stEl.textContent = d.progress != null ? `${d.stage} (${d.progress}%)` : d.stage;
        }
        scrollToBottom();
      });

      es.addEventListener("thinking", (ev) => {
        const d = JSON.parse(ev.data || "{}");
        accThink += d.delta || "";
        const tb = streamRow.querySelector("#liveThinkBox");
        const body = streamRow.querySelector("#liveThinkBody");
        if (tb && body) {
          tb.classList.remove("hidden");
          body.textContent = accThink;
        }
        scrollToBottom();
      });

      es.addEventListener("delta", (ev) => {
        const d = JSON.parse(ev.data || "{}");
        accContent += d.delta || "";
        streamRow.querySelector("#liveAgentStatus")?.classList.add("hidden");
        const mdEl = streamRow.querySelector("#liveMdBody");
        if (mdEl) {
          mdEl.innerHTML = renderMarkdown(accContent);
          wireCopyButtons(mdEl);
        }
        scrollToBottom();
      });

      es.addEventListener("tool_call", (ev) => {
        const tc = JSON.parse(ev.data || "{}");
        const wrap = streamRow.querySelector("#liveToolsWrap");
        if (wrap) {
          wrap.insertAdjacentHTML("beforeend", buildToolCardHtml(tc));
        }
        scrollToBottom();
      });

      es.addEventListener("approval_required", (ev) => {
        const ap = JSON.parse(ev.data || "{}");
        const wrap = streamRow.querySelector("#liveToolsWrap");
        const card = document.createElement("div");
        card.className = "approval-card";
        card.innerHTML = `
          <div class="ap-head">🛡️ 安全确认：Agent 请求执行 <strong>${escapeHtml(ap.name)}</strong></div>
          <pre class="ap-args">${escapeHtml(JSON.stringify(ap.arguments || {}, null, 2))}</pre>
          <div class="ap-btns">
            <button class="btn-accent ap-yes">✅ 允许执行</button>
            <button class="btn-ghost-box ap-no">✕ 拒绝</button>
          </div>`;
        card.querySelector(".ap-yes").addEventListener("click", async () => {
          card.innerHTML = `<div class="ap-head">✅ 已批准执行 ${escapeHtml(ap.name)}…</div>`;
          await api("/api/agent/approve", { method: "POST", body: JSON.stringify({ approval_id: ap.approval_id, approved: true }) });
        });
        card.querySelector(".ap-no").addEventListener("click", async () => {
          card.innerHTML = `<div class="ap-head">✕ 已拒绝执行 ${escapeHtml(ap.name)}</div>`;
          await api("/api/agent/approve", { method: "POST", body: JSON.stringify({ approval_id: ap.approval_id, approved: false }) });
        });
        wrap?.appendChild(card);
        scrollToBottom();
      });

      es.addEventListener("tool_result", (ev) => {
        const tr = JSON.parse(ev.data || "{}");
        const wrap = streamRow.querySelector("#liveToolsWrap");
        if (wrap) {
          const existing = wrap.querySelector(`[data-call-id="${tr.call_id}"]`);
          const html = buildToolCardHtml(tr);
          if (existing) existing.outerHTML = html;
          else wrap.insertAdjacentHTML("beforeend", html);
          wireMediaJumpButtons(wrap);
        }
        if (tr.result?.media_item) {
          window.StudioBridge?.refreshCanvas?.();
        }
        scrollToBottom();
      });

      es.addEventListener("done", (ev) => {
        const d = JSON.parse(ev.data || "{}");
        es.close();
        agentState.eventSource = null;
        agentState.activeRunId = null;
        setSendingState(false);
        if (d.message) {
          agentState.messages.push(d.message);
        }
        renderMessages();
        loadSessions();
        window.StudioBridge?.refreshStatus?.(true);
      });

      es.addEventListener("error", (ev) => {
        let errMsg = "对话连接中断";
        try {
          const d = JSON.parse(ev.data || "{}");
          if (d.error) errMsg = d.error;
        } catch (_) {}
        es.close();
        agentState.eventSource = null;
        agentState.activeRunId = null;
        setSendingState(false);
        streamRow.querySelector("#liveAgentStatus").textContent = `❌ ${errMsg}`;
        toast(errMsg, "error");
        window.StudioBridge?.refreshStatus?.(true);
      });
    } catch (e) {
      setSendingState(false);
      agentState.activeRunId = null;
      toast(e.message, "error");
    }
  }

  $("chatSendBtn").addEventListener("click", sendChatMessage);
  $("chatInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });

  // Boot Agent view
  switchWorkspaceView("agent");
  loadSessions();
})();
