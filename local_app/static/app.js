(() => {
  const $ = (id) => document.getElementById(id);

  const MODELS = {
    video: [
      { id: "ltx-video", label: "LTX-Video 2B · 极速省单元 (T4/L4/A100)", steps: 28, guidance: 3.5, fps: 24, supportsI2V: true },
      { id: "wan2.2-5b", label: "阿里 Wan 2.2 5B · 电影级画质 (L4/A100)", steps: 35, guidance: 5.0, fps: 16, supportsI2V: true },
      { id: "wan2.1-1.3b", label: "阿里 Wan 2.1 1.3B · 轻量文生视频 (T4/L4)", steps: 30, guidance: 5.0, fps: 16, supportsI2V: false },
    ],
    image: [
      { id: "realvis-xl", label: "RealVisXL V5.0 · 电影写实首选 (T4/L4秒出)", steps: 25, guidance: 5.5, supportsI2V: false },
      { id: "z-image-turbo", label: "阿里 Z-Image Turbo · 9步中英双语 SOTA", steps: 9, guidance: 0.0, supportsI2V: false },
      { id: "shuttle-3.1", label: "Shuttle 3.1 (FLUX架构) · 4步美学 (L4/高内存推荐)", steps: 4, guidance: 0.0, supportsI2V: false },
      { id: "sdxl-base", label: "SDXL 1.0 Base · 经典稳定", steps: 25, guidance: 7.5, supportsI2V: false },
    ],
  };

  const RATIOS = {
    image: {
      "16:9": [1024, 576],
      "9:16": [576, 1024],
      "1:1": [1024, 1024],
      "4:3": [896, 672],
    },
    video: {
      "16:9": [704, 480],
      "9:16": [480, 704],
      "1:1": [512, 512],
      "4:3": [640, 480],
    },
  };

  const state = {
    mode: "video",
    tool: "select", // select | pan
    spacePressed: false,
    pan: { x: 80, y: 76 },
    scale: 1,
    items: [],
    activeJobs: [],
    selectedId: null,
    refItem: null, // { id, local_url, filename }
    status: null,
    lightboxItem: null,
  };

  function toast(msg, type = "info") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    $("toastStack").appendChild(el);
    setTimeout(() => el.remove(), 4500);
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

  // ==================== INFINITE CANVAS TRANSFORM ====================
  function applyTransform() {
    $("canvasWorld").style.transform = `translate(${state.pan.x}px, ${state.pan.y}px) scale(${state.scale})`;
    const gridSize = 28 * state.scale;
    $("canvasGrid").style.backgroundSize = `${gridSize}px ${gridSize}px`;
    $("canvasGrid").style.backgroundPosition = `${state.pan.x}px ${state.pan.y}px`;
    $("zoomResetBtn").textContent = `${Math.round(state.scale * 100)}%`;
  }

  function screenToWorld(clientX, clientY) {
    const rect = $("canvasViewport").getBoundingClientRect();
    return {
      x: (clientX - rect.left - state.pan.x) / state.scale,
      y: (clientY - rect.top - state.pan.y) / state.scale,
    };
  }

  function setZoom(newScale, anchorX, anchorY) {
    const clamped = Math.min(2.0, Math.max(0.25, newScale));
    const rect = $("canvasViewport").getBoundingClientRect();
    const cx = anchorX ?? rect.width / 2;
    const cy = anchorY ?? rect.height / 2;
    const wx = (cx - state.pan.x) / state.scale;
    const wy = (cy - state.pan.y) / state.scale;
    state.scale = clamped;
    state.pan.x = cx - wx * clamped;
    state.pan.y = cy - wy * clamped;
    applyTransform();
  }

  function fitToView() {
    const allNodes = [
      ...state.items.map((it) => ({ x: it.x || 0, y: it.y || 0, w: 340, h: 300 })),
      ...state.activeJobs.map((j) => ({ x: j.x || 0, y: j.y || 0, w: 340, h: 300 })),
    ];
    if (!allNodes.length) {
      state.pan = { x: 80, y: 76 };
      state.scale = 1;
      applyTransform();
      return;
    }
    const minX = Math.min(...allNodes.map((n) => n.x));
    const minY = Math.min(...allNodes.map((n) => n.y));
    const maxX = Math.max(...allNodes.map((n) => n.x + n.w));
    const maxY = Math.max(...allNodes.map((n) => n.y + n.h));
    const vw = window.innerWidth - 160;
    const vh = window.innerHeight - 280;
    const scale = Math.min(1.15, Math.max(0.3, Math.min(vw / (maxX - minX + 80), vh / (maxY - minY + 80))));
    state.scale = scale;
    state.pan.x = (window.innerWidth - (maxX - minX) * scale) / 2 - minX * scale;
    state.pan.y = (window.innerHeight - 120 - (maxY - minY) * scale) / 2 - minY * scale;
    applyTransform();
  }

  // Pan & Wheel interactions
  let isPanning = false;
  let panStart = { x: 0, y: 0 };

  $("canvasViewport").addEventListener("pointerdown", (e) => {
    const onEmpty = e.target === $("canvasViewport") || e.target === $("canvasGrid") || e.target === $("canvasWorld");
    if (onEmpty) {
      selectNode(null);
    }
    if (state.tool === "pan" || state.spacePressed || e.button === 1 || (onEmpty && e.button === 0)) {
      isPanning = true;
      panStart = { x: e.clientX - state.pan.x, y: e.clientY - state.pan.y };
      $("canvasViewport").classList.add("panning-active");
    }
  });

  window.addEventListener("pointermove", (e) => {
    if (!isPanning) return;
    state.pan.x = e.clientX - panStart.x;
    state.pan.y = e.clientY - panStart.y;
    applyTransform();
  });

  window.addEventListener("pointerup", () => {
    if (isPanning) {
      isPanning = false;
      $("canvasViewport").classList.remove("panning-active");
    }
  });

  $("canvasViewport").addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        const delta = -e.deltaY * 0.003;
        setZoom(state.scale * (1 + delta), e.clientX, e.clientY);
      } else {
        state.pan.x -= e.deltaX;
        state.pan.y -= e.deltaY;
        applyTransform();
      }
    },
    { passive: false }
  );

  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && document.activeElement?.tagName !== "TEXTAREA" && document.activeElement?.tagName !== "INPUT") {
      state.spacePressed = true;
      $("canvasViewport").classList.add("panning-mode");
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
      state.spacePressed = false;
      if (state.tool !== "pan") $("canvasViewport").classList.remove("panning-mode");
    }
  });

  // Left tool rail & Zoom buttons
  $("toolSelectBtn").addEventListener("click", () => {
    state.tool = "select";
    $("toolSelectBtn").classList.add("active");
    $("toolPanBtn").classList.remove("active");
    $("canvasViewport").classList.remove("panning-mode");
  });
  $("toolPanBtn").addEventListener("click", () => {
    state.tool = "pan";
    $("toolPanBtn").classList.add("active");
    $("toolSelectBtn").classList.remove("active");
    $("canvasViewport").classList.add("panning-mode");
  });
  $("zoomInBtn").addEventListener("click", () => setZoom(state.scale + 0.15));
  $("zoomOutBtn").addEventListener("click", () => setZoom(state.scale - 0.15));
  $("zoomResetBtn").addEventListener("click", () => setZoom(1.0));
  $("fitViewBtn").addEventListener("click", fitToView);

  $("tidyLayoutBtn").addEventListener("click", async () => {
    try {
      const res = await api("/api/canvas/layout", { method: "POST" });
      state.items = res.items || [];
      renderCanvas();
      fitToView();
      toast("已按图文关联重新整理画布布局", "success");
    } catch (e) {
      toast(e.message, "error");
    }
  });

  $("openFolderBtn").addEventListener("click", async () => {
    try {
      await api("/api/open-outputs", { method: "POST" });
    } catch (e) {
      toast(e.message, "error");
    }
  });

  // ==================== CANVAS RENDERING & SVG CONNECTORS ====================
  function selectNode(id) {
    state.selectedId = id;
    document.querySelectorAll(".canvas-node").forEach((el) => {
      el.classList.toggle("selected", el.dataset.id === id);
    });
  }

  function renderConnectors() {
    const svg = $("connectorLayer");
    const defs = `
      <defs>
        <linearGradient id="connGrad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#6366f1"/>
          <stop offset="100%" stop-color="#a855f7"/>
        </linearGradient>
      </defs>`;
    const nodeMap = new Map();
    for (const it of state.items) nodeMap.set(it.id, { x: it.x || 0, y: it.y || 0, w: 340, h: 260 });
    for (const j of state.activeJobs) nodeMap.set(j.job_id, { x: j.x || 0, y: j.y || 0, w: 340, h: 260 });

    const paths = [];
    const drawEdge = (parentId, childId, animated = false) => {
      const p = nodeMap.get(parentId);
      const c = nodeMap.get(childId);
      if (!p || !c) return;
      const x1 = p.x + p.w;
      const y1 = p.y + 135;
      const x2 = c.x;
      const y2 = c.y + 135;
      const dx = Math.max(80, Math.abs(x2 - x1) * 0.45);
      const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
      paths.push(`<path class="connector-path ${animated ? "animated" : ""}" d="${d}" />`);
    };

    for (const it of state.items) {
      if (it.parent_id) drawEdge(it.parent_id, it.id, false);
    }
    for (const j of state.activeJobs) {
      if (j.parent_id) drawEdge(j.parent_id, j.job_id, true);
    }
    svg.innerHTML = defs + paths.join("");
  }

  function attachNodeDrag(el, item) {
    const handle = el.querySelector(".node-drag-header");
    let dragging = false;
    let startMouse = { x: 0, y: 0 };
    let startPos = { x: 0, y: 0 };

    handle.addEventListener("pointerdown", (e) => {
      if (state.tool === "pan" || state.spacePressed) return;
      e.stopPropagation();
      selectNode(item.id);
      dragging = true;
      startMouse = { x: e.clientX, y: e.clientY };
      startPos = { x: item.x || 0, y: item.y || 0 };
      handle.setPointerCapture(e.pointerId);
    });

    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      item.x = Math.round(startPos.x + (e.clientX - startMouse.x) / state.scale);
      item.y = Math.round(startPos.y + (e.clientY - startMouse.y) / state.scale);
      el.style.left = `${item.x}px`;
      el.style.top = `${item.y}px`;
      renderConnectors();
    });

    handle.addEventListener("pointerup", () => {
      if (!dragging) return;
      dragging = false;
      api(`/api/canvas/items/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ x: item.x, y: item.y }),
      }).catch(() => {});
    });
  }

  function renderCanvas() {
    const layer = $("nodesLayer");
    layer.innerHTML = "";
    const totalCount = state.items.length + state.activeJobs.length;
    $("nodeCountLabel").textContent = `${totalCount} 个画布节点`;
    $("canvasWelcome").classList.toggle("hidden", totalCount > 0);

    // 1. Render completed / material items
    for (const item of state.items) {
      const el = document.createElement("div");
      el.className = `canvas-node ${state.selectedId === item.id ? "selected" : ""}`;
      el.dataset.id = item.id;
      el.style.left = `${item.x || 120}px`;
      el.style.top = `${item.y || 120}px`;

      const isVideo = item.kind === "video" || item.filename.endsWith(".mp4");
      const kindLabel = item.kind === "material" ? "本地素材" : isVideo ? "VIDEO" : "IMAGE";
      const tagClass = item.kind === "material" ? "material" : isVideo ? "video" : "image";
      const subMeta = item.elapsed_seconds ? `${item.model_id} · ${item.elapsed_seconds}s` : item.model_id || "";
      const cloudBadge =
        item.drive_sync === "synced"
          ? `<span class="cloud-badge" title="已同步至 Google Drive">☁️ Drive</span>`
          : item.drive_sync === "syncing"
          ? `<span class="cloud-badge syncing" title="正在上传到 Google Drive">⏳ 同步中</span>`
          : "";

      const mediaHtml = isVideo
        ? `<video src="${item.local_url}" loop muted playsinline controls></video>`
        : `<img src="${item.local_url}" alt="Canvas media" loading="lazy" />`;

      el.innerHTML = `
        <div class="node-toolbar">
          ${!isVideo ? `<button class="nt-btn accent to-video-btn">🎬 以此图生视频</button>` : ""}
          <button class="nt-btn preview-btn">🔍 预览</button>
          <button class="nt-btn reuse-btn">🔁 提示词</button>
          ${item.drive_web_link ? `<a class="nt-btn" href="${item.drive_web_link}" target="_blank" title="在 Google Drive 中查看">☁️</a>` : ""}
          <a class="nt-btn" href="${item.local_url}" download="${item.filename}" title="下载到本地">⬇️</a>
          <button class="nt-btn danger del-btn">🗑️</button>
        </div>
        <div class="node-drag-header">
          <div><span class="node-tag ${tagClass}">${kindLabel}</span>${cloudBadge}</div>
          <span class="node-submeta">${subMeta}</span>
        </div>
        <div class="node-media">${mediaHtml}</div>
        <div class="node-footer">
          <div class="node-prompt" title="${(item.prompt || "").replace(/"/g, "&quot;")}">${item.prompt || item.filename}</div>
        </div>
      `;

      el.addEventListener("pointerdown", (e) => {
        e.stopPropagation();
        selectNode(item.id);
      });

      el.querySelector(".node-media").addEventListener("dblclick", () => openLightbox(item));
      const toVid = el.querySelector(".to-video-btn");
      if (toVid) toVid.addEventListener("click", () => attachReferenceItem(item));
      el.querySelector(".preview-btn").addEventListener("click", () => openLightbox(item));
      el.querySelector(".reuse-btn").addEventListener("click", () => reusePrompt(item));
      el.querySelector(".del-btn").addEventListener("click", async () => {
        if (!confirm("确定从画布和本地删除这个文件吗？")) return;
        await api(`/api/canvas/items/${item.id}`, { method: "DELETE" });
        if (state.refItem?.id === item.id) clearReferenceItem();
        state.items = state.items.filter((x) => x.id !== item.id);
        renderCanvas();
      });

      attachNodeDrag(el, item);
      layer.appendChild(el);
    }

    // 2. Render live generating job cards
    for (const job of state.activeJobs) {
      const el = document.createElement("div");
      el.className = "canvas-node generating";
      el.dataset.id = job.job_id;
      el.style.left = `${job.x || 120}px`;
      el.style.top = `${job.y || 120}px`;
      const pct = Math.min(100, Math.max(2, Number(job.progress || 2)));

      el.innerHTML = `
        <div class="node-drag-header">
          <span class="node-tag ${job.task === "video" ? "video" : "image"}">生成中 · ${job.model_id}</span>
          <span class="node-submeta">${Number(job.elapsed_seconds || 0).toFixed(0)}s</span>
        </div>
        <div class="gen-stage-canvas">
          <div class="gen-spinner"></div>
          <div class="gen-stage-text">${job.stage || "正在云端 GPU 渲染..."}</div>
          <div class="gen-progress-wrap">
            <div class="gen-progress-track">
              <div class="gen-progress-fill" style="width:${pct}%"></div>
            </div>
            <div class="gen-progress-meta">
              <span>${job.gpu_name ? job.gpu_name : "Colab GPU"}</span>
              <span>${pct}%</span>
            </div>
          </div>
        </div>
        <div class="node-footer">
          <div class="node-prompt">${job.prompt || ""}</div>
        </div>
      `;
      layer.appendChild(el);
    }

    renderConnectors();
  }

  // ==================== MATERIAL IMPORT (DRAG & DROP OR FILE PICKER) ====================
  async function uploadMaterialFile(file, worldPos = null) {
    if (!file || !file.type.startsWith("image/")) {
      return toast("请拖入图片文件（PNG / JPG / WebP）", "error");
    }
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    try {
      const payload = { filename: file.name, data_base64: dataUrl };
      if (worldPos) {
        payload.x = Math.round(worldPos.x - 170);
        payload.y = Math.round(worldPos.y - 120);
      }
      const res = await api("/api/materials/upload", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.items.unshift(res.item);
      attachReferenceItem(res.item);
      renderCanvas();
      toast("素材图已导入画布并关联为参考首帧", "success");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  $("importMaterialBtn").addEventListener("click", () => $("materialFileInput").click());
  $("welcomeUploadBtn").addEventListener("click", () => $("materialFileInput").click());
  $("refSlot").addEventListener("click", (e) => {
    if (e.target.id === "refClearBtn") return;
    $("materialFileInput").click();
  });
  $("materialFileInput").addEventListener("change", () => {
    const f = $("materialFileInput").files?.[0];
    if (f) uploadMaterialFile(f);
    $("materialFileInput").value = "";
  });

  let dragCounter = 0;
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragCounter++;
    $("dropOverlay").classList.remove("hidden");
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", () => {
    dragCounter = Math.max(0, dragCounter - 1);
    if (dragCounter === 0) $("dropOverlay").classList.add("hidden");
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    dragCounter = 0;
    $("dropOverlay").classList.add("hidden");
    const file = e.dataTransfer?.files?.[0];
    if (file) {
      const wp = screenToWorld(e.clientX, e.clientY);
      uploadMaterialFile(file, wp);
    }
  });

  function attachReferenceItem(item) {
    state.refItem = item;
    $("refThumb").src = item.local_url;
    $("refEmpty").classList.add("hidden");
    $("refFilled").classList.remove("hidden");
    if (state.mode !== "video") setMode("video");
    if ($("modelSelect").value === "wan2.1-1.3b") {
      $("modelSelect").value = "ltx-video";
      syncModelDefaults();
    }
    if (!$("promptInput").value.trim() && item.prompt && item.kind !== "material") {
      $("promptInput").value = `${item.prompt}, smooth cinematic camera motion`;
    }
  }

  function clearReferenceItem() {
    state.refItem = null;
    $("refFilled").classList.add("hidden");
    $("refEmpty").classList.remove("hidden");
  }
  $("refClearBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    clearReferenceItem();
  });

  function reusePrompt(item) {
    $("promptInput").value = item.prompt || "";
    $("promptInput").focus();
    toast("已将提示词填入底部创作栏", "success");
  }

  // ==================== COMPOSER MODE & MODELS ====================
  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    $("durationSelect").classList.toggle("hidden", mode !== "video");
    $("refSlot").classList.toggle("hidden", mode !== "video");

    const sel = $("modelSelect");
    sel.innerHTML = MODELS[mode].map((m) => `<option value="${m.id}">${m.label}</option>`).join("");
    syncModelDefaults();
  }

  function syncModelDefaults() {
    const m = MODELS[state.mode].find((x) => x.id === $("modelSelect").value) || MODELS[state.mode][0];
    if (!m) return;
    $("stepsInput").value = m.steps;
    $("guidanceInput").value = m.guidance;
  }

  document.querySelectorAll(".mode-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("modelSelect").addEventListener("change", syncModelDefaults);

  // Welcome sample chips
  document.querySelectorAll(".sample-chip[data-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setMode(btn.dataset.mode);
      $("promptInput").value = btn.dataset.prompt;
      $("promptInput").focus();
    });
  });

  // Advanced popover
  $("advBtn").addEventListener("click", () => $("advPopover").classList.toggle("hidden"));
  $("closeAdvBtn").addEventListener("click", () => $("advPopover").classList.add("hidden"));
  $("autoStopInput").addEventListener("change", () => {
    api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ auto_stop_minutes: Number($("autoStopInput").value || 0) }),
    }).then(() => refreshStatus(false));
  });

  // ==================== LIVE GPU & QUOTA HUD ====================
  function renderStatus(snap) {
    state.status = snap;
    if (snap.preferred_gpu) $("preferredGpuSelect").value = snap.preferred_gpu;
    if (snap.auto_stop_minutes != null) $("autoStopInput").value = snap.auto_stop_minutes;

    // 1. Account button
    const accBtn = $("accountBtn");
    if (snap.authenticated) {
      accBtn.textContent = snap.user_email ? snap.user_email.split("@")[0] : "已登录 Google";
      accBtn.className = "hud-btn hud-btn-account";
      accBtn.title = `已绑定 ${snap.user_email || "Google 账号"}（点击可重新授权）`;
    } else {
      accBtn.textContent = "登录 Google 账号";
      accBtn.className = "hud-btn hud-btn-primary";
    }

    // 2. GPU Status & Toggle Button
    const sess = snap.session || {};
    const toggleBtn = $("toggleGpuBtn");
    if (sess.active) {
      $("gpuDot").className = "status-dot online";
      const gpuLabel = sess.gpu_name_detail || `NVIDIA ${sess.accelerator || "GPU"}`;
      const vramTxt = sess.vram_total_gb
        ? `${sess.vram_used_gb ?? 0}/${sess.vram_total_gb}GB`
        : sess.vram_label || "";
      $("gpuStatusText").textContent = `${gpuLabel}${vramTxt ? ` · ${vramTxt}` : ""}`;
      toggleBtn.textContent = "释放显卡";
      toggleBtn.className = "hud-btn hud-btn-danger";
      toggleBtn.disabled = Boolean(snap.active_job_id);
    } else {
      $("gpuDot").className = "status-dot offline";
      $("gpuStatusText").textContent = snap.authenticated ? "待机 · 生成自动开机" : "未登录";
      toggleBtn.textContent = "立即开机";
      toggleBtn.className = "hud-btn hud-btn-ghost";
      toggleBtn.disabled = !snap.authenticated;
    }

    // 3. Real Compute Units Balance & Remaining Hours
    const u = snap.usage || {};
    if (!snap.authenticated) {
      $("quotaText").textContent = "登录后显示实时余额与时长";
    } else if (u.balance_cu != null) {
      const pref = $("preferredGpuSelect").value || "L4";
      let hoursTxt = "";
      if (u.remaining_hours_live != null) {
        hoursTxt = `当前费率剩余约 ${u.remaining_hours_live} 小时`;
      } else if (u.est_hours_by_gpu && u.est_hours_by_gpu[pref] != null) {
        hoursTxt = `按 ${pref} 约可用 ${u.est_hours_by_gpu[pref]} 小时`;
      }
      $("quotaText").textContent = `⚡ ${u.balance_cu.toFixed(1)} 单元${hoursTxt ? ` · ${hoursTxt}` : ""}`;
    } else {
      $("quotaText").textContent = "已连接 · 等待同步余额";
    }

    // 4. Idle Auto-Stop Countdown
    const autoPill = $("autoStopPill");
    if (sess.active && !snap.active_job_id && snap.auto_stop_remaining_seconds != null) {
      const m = Math.floor(snap.auto_stop_remaining_seconds / 60);
      const s = snap.auto_stop_remaining_seconds % 60;
      $("autoStopText").textContent = `${m}m${s < 10 ? "0" : ""}${s}s 后自动释放`;
      autoPill.classList.remove("hidden");
    } else {
      autoPill.classList.add("hidden");
    }

    // 5. Google Drive Sync Status
    const drv = snap.drive || {};
    $("driveAutoSyncCheck").checked = drv.enabled !== false;
    const drvBtn = $("driveBtn");
    if (drv.syncing) {
      $("driveBtnText").textContent = "Drive 同步中…";
      drvBtn.classList.remove("synced");
    } else if (snap.authenticated && drv.enabled) {
      $("driveBtnText").textContent = `Drive (${drv.synced_count || 0}/${drv.total_count || 0})`;
      drvBtn.classList.toggle("synced", Boolean(drv.connected || drv.synced_count > 0));
    } else {
      $("driveBtnText").textContent = "Drive 云盘";
      drvBtn.classList.remove("synced");
    }

    if (!snap.authenticated) {
      $("driveStatText").textContent = "请先登录 Google 账号以启用 Google Drive 同步";
      $("driveSyncNowBtn").disabled = true;
    } else {
      $("driveSyncNowBtn").disabled = Boolean(drv.syncing);
      $("driveStatText").textContent = drv.last_error
        ? `同步提示：${drv.last_error}`
        : `已云端备份 ${drv.synced_count || 0} / ${drv.total_count || 0} 个素材与作品`;
    }

    const folderLink = $("driveOpenFolderLink");
    if (drv.folder_url) {
      folderLink.href = drv.folder_url;
      folderLink.classList.remove("hidden");
    } else {
      folderLink.classList.add("hidden");
    }
  }

  async function refreshStatus(force = false) {
    try {
      const snap = await api(`/api/status${force ? "?force=1" : ""}`);
      renderStatus(snap);
    } catch (_) {}
  }

  $("preferredGpuSelect").addEventListener("change", async () => {
    const gpu = $("preferredGpuSelect").value;
    await api("/api/settings", { method: "POST", body: JSON.stringify({ preferred_gpu: gpu }) });
    if (state.status) renderStatus({ ...state.status, preferred_gpu: gpu });
  });

  $("toggleGpuBtn").addEventListener("click", async () => {
    if (!state.status?.authenticated) return openAuthModal();
    const isOnline = Boolean(state.status?.session?.active);
    const btn = $("toggleGpuBtn");
    btn.disabled = true;
    try {
      if (isOnline) {
        btn.textContent = "释放中...";
        const snap = await api("/api/gpu/stop", { method: "POST" });
        renderStatus(snap);
        toast("已释放云端 GPU，已停止计算单元扣费！", "success");
      } else {
        const gpu = $("preferredGpuSelect").value;
        btn.textContent = "申请中...";
        toast(`正在向 Colab 申请 ${gpu} 云端显卡...`);
        const snap = await api("/api/gpu/start", { method: "POST", body: JSON.stringify({ gpu }) });
        renderStatus(snap);
        toast(`云端显卡已就绪：${snap.session?.accelerator || gpu}`, "success");
      }
    } catch (e) {
      toast(e.message, "error");
      refreshStatus(true);
    } finally {
      btn.disabled = false;
    }
  });

  // ==================== GOOGLE DRIVE SYNC CONTROLS ====================
  $("driveBtn").addEventListener("click", () => $("drivePopover").classList.toggle("hidden"));
  $("closeDriveBtn").addEventListener("click", () => $("drivePopover").classList.add("hidden"));

  $("driveAutoSyncCheck").addEventListener("change", async () => {
    const enabled = $("driveAutoSyncCheck").checked;
    await api("/api/settings", { method: "POST", body: JSON.stringify({ drive_sync_enabled: enabled }) });
    toast(enabled ? "已开启自动同步到 Google Drive" : "已暂停自动同步（可随时手动同步）", "success");
    refreshStatus(false);
  });

  $("driveSyncNowBtn").addEventListener("click", async () => {
    if (!state.status?.authenticated) return openAuthModal();
    const btn = $("driveSyncNowBtn");
    btn.disabled = true;
    btn.textContent = "正在双向同步 Google Drive…";
    try {
      const res = await api("/api/drive/sync", { method: "POST" });
      state.items = res.items || state.items;
      renderCanvas();
      await refreshStatus(false);
      toast(`云盘同步完成！上传 ${res.uploaded} 个，从云盘恢复 ${res.downloaded} 个`, "success");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "🔄 立即双向同步云盘";
    }
  });

  // ==================== IN-APP GOOGLE OAUTH FLOW ====================
  function openAuthModal() {
    $("oauthCodeInput").value = "";
    $("authModal").classList.remove("hidden");
  }
  function closeAuthModal() {
    $("authModal").classList.add("hidden");
  }

  $("accountBtn").addEventListener("click", openAuthModal);
  $("closeAuthModalBtn").addEventListener("click", closeAuthModal);

  $("startOauthBtn").addEventListener("click", async () => {
    const btn = $("startOauthBtn");
    btn.disabled = true;
    btn.textContent = "正在生成授权链接...";
    try {
      const res = await api("/api/auth/start", { method: "POST" });
      if (res.already_authenticated) {
        closeAuthModal();
        await refreshStatus(true);
        toast(`已登录 ${res.email || "Google 账号"}`, "success");
      } else if (res.auth_url) {
        window.open(res.auth_url, "_blank");
        toast("已在新标签页打开 Google 授权页，完成后请粘贴授权码", "success");
        $("oauthCodeInput").focus();
      }
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "🔐 打开 Google 官方授权页";
    }
  });

  $("completeOauthBtn").addEventListener("click", async () => {
    const code = $("oauthCodeInput").value.trim();
    if (!code) return toast("请先粘贴 Google 授权码", "error");
    const btn = $("completeOauthBtn");
    btn.disabled = true;
    btn.textContent = "验证中...";
    try {
      const res = await api("/api/auth/complete", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      closeAuthModal();
      await refreshStatus(true);
      toast(`绑定成功！欢迎 ${res.email || ""}，正在同步你的 Google Drive 画布…`, "success");
      setTimeout(async () => {
        try {
          const canvas = await api("/api/canvas");
          state.items = canvas.items || state.items;
          renderCanvas();
          await refreshStatus(false);
        } catch (_) {}
      }, 2800);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "完成绑定";
    }
  });

  // ==================== GENERATE & LIVE PROGRESS POLLING ====================
  async function submitGeneration() {
    if (!state.status?.authenticated) {
      openAuthModal();
      return toast("请先完成一次 Google 账号绑定", "error");
    }
    const prompt = $("promptInput").value.trim();
    if (!prompt) return toast("请输入画面提示词", "error");

    const modelId = $("modelSelect").value;
    const modelDef = MODELS[state.mode].find((m) => m.id === modelId);
    const ratio = $("ratioSelect").value;
    const [width, height] = RATIOS[state.mode][ratio] || [704, 480];

    const payload = {
      task: state.mode,
      model_id: modelId,
      gpu: $("preferredGpuSelect").value,
      prompt,
      negative_prompt: $("negPromptInput").value.trim(),
      width,
      height,
      steps: Number($("stepsInput").value || modelDef?.steps || 25),
      guidance_scale: Number($("guidanceInput").value ?? modelDef?.guidance ?? 3.5),
      seed: Number($("seedInput").value ?? -1),
      num_frames: Number($("durationSelect").value || 49),
      fps: modelDef?.fps || 24,
      parent_id: state.mode === "video" && modelDef?.supportsI2V && state.refItem ? state.refItem.id : null,
    };

    const btn = $("generateBtn");
    btn.disabled = true;
    $("generateBtnText").textContent = "渲染中…";

    try {
      const job = await api("/api/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.activeJobs.push(job);
      renderCanvas();

      // Smoothly pan toward the new card if it's the first one
      if (state.items.length === 0) fitToView();

      await pollJob(job.job_id);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      $("generateBtnText").textContent = "生成";
      refreshStatus(true);
    }
  }

  async function pollJob(jobId) {
    while (true) {
      await new Promise((r) => setTimeout(r, 1200));
      const job = await api(`/api/jobs/${jobId}`);
      const idx = state.activeJobs.findIndex((j) => j.job_id === jobId);

      if (job.status === "completed") {
        if (idx >= 0) state.activeJobs.splice(idx, 1);
        if (job.item) {
          state.items.unshift(job.item);
          selectNode(job.item.id);
        }
        renderCanvas();
        toast("生成完成！成品已自动落入无限画布并保存到本地", "success");
        return;
      }

      if (job.status === "failed") {
        if (idx >= 0) state.activeJobs.splice(idx, 1);
        renderCanvas();
        toast(`生成失败：${job.error || "请检查显卡或参数"}`, "error");
        return;
      }

      if (idx >= 0) {
        state.activeJobs[idx] = job;
        renderCanvas();
      }
    }
  }

  $("generateBtn").addEventListener("click", submitGeneration);
  $("promptInput").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submitGeneration();
    }
  });

  // ==================== CINEMA LIGHTBOX ====================
  function openLightbox(item) {
    state.lightboxItem = item;
    const isVideo = item.kind === "video" || item.filename.endsWith(".mp4");
    $("lbKindBadge").textContent = item.kind === "material" ? "参考素材" : isVideo ? "AI VIDEO" : "AI IMAGE";
    $("lightboxMedia").innerHTML = isVideo
      ? `<video src="${item.local_url}" controls autoplay loop playsinline></video>`
      : `<img src="${item.local_url}" alt="Preview" />`;
    $("lbPrompt").textContent = item.prompt || item.filename;
    $("lbMeta").innerHTML = [
      item.model_id ? `模型：${item.model_id}` : "",
      item.gpu_name ? `渲染显卡：${item.gpu_name}` : "",
      item.width && item.height ? `分辨率：${item.width} × ${item.height}` : "",
      item.seed != null ? `随机种子：${item.seed}` : "",
      item.elapsed_seconds ? `耗时：${item.elapsed_seconds}s` : "",
      `本地文件：outputs/${item.filename}`,
    ].filter(Boolean).join("<br/>");

    $("lbDownloadLink").href = item.local_url;
    $("lbDownloadLink").download = item.filename;
    const lbDrive = $("lbDriveLink");
    if (item.drive_web_link) {
      lbDrive.href = item.drive_web_link;
      lbDrive.classList.remove("hidden");
    } else {
      lbDrive.classList.add("hidden");
    }
    $("lbToVideoBtn").classList.toggle("hidden", isVideo);
    $("lightboxModal").classList.remove("hidden");
  }

  function closeLightbox() {
    $("lightboxModal").classList.add("hidden");
    $("lightboxMedia").innerHTML = "";
  }

  $("closeLightboxBtn").addEventListener("click", closeLightbox);
  $("lightboxModal").addEventListener("click", (e) => {
    if (e.target === $("lightboxModal")) closeLightbox();
  });
  $("lbToVideoBtn").addEventListener("click", () => {
    if (state.lightboxItem) {
      attachReferenceItem(state.lightboxItem);
      closeLightbox();
    }
  });
  $("lbReusePromptBtn").addEventListener("click", () => {
    if (state.lightboxItem) {
      reusePrompt(state.lightboxItem);
      closeLightbox();
    }
  });

  // ==================== BOOT ====================
  async function boot() {
    setMode("video");
    applyTransform();
    try {
      const canvas = await api("/api/canvas");
      state.items = canvas.items || [];
      state.activeJobs = canvas.active_jobs || [];
      renderCanvas();
      if (state.items.length > 0) fitToView();
      for (const j of state.activeJobs) pollJob(j.job_id);
    } catch (_) {}

    await refreshStatus(true);
    setInterval(() => refreshStatus(false), 12000);
  }

  boot();
})();
