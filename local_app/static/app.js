(() => {
  const state = {
    connected: false,
    health: null,
    config: {},
    idleDismissed: false,
    lastImageItem: null,
    i2vBase64: null,
    i2vGalleryId: null,
    activePoll: null,
  };

  const $ = (id) => document.getElementById(id);

  function toast(msg, type = "info") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    $("toastContainer").appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `请求失败 (${res.status})`);
    }
    return data;
  }

  function formatDuration(sec) {
    if (sec == null) return "--";
    const s = Math.max(0, Math.floor(sec));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rem = s % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${rem}s`;
    return `${rem}s`;
  }

  // Tabs
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "gallery") loadGallery();
    });
  });

  // Model parameter defaults
  $("imgModel").addEventListener("change", () => {
    const m = $("imgModel").value;
    if (m === "flux-schnell") {
      $("imgSteps").value = 4;
      $("imgGuidance").value = 0;
      $("imgNegField").classList.add("hidden");
      $("imgModelHint").textContent = "FLUX.1-schnell：4 步极速出高清图，无需反向提示词，非常省计算单元。";
    } else {
      $("imgSteps").value = 25;
      $("imgGuidance").value = 7.5;
      $("imgNegField").classList.remove("hidden");
      $("imgModelHint").textContent = "SDXL 1.0：经典高质模型，支持反向提示词，T4 上显存占用极低。";
    }
  });
  $("imgModel").dispatchEvent(new Event("change"));

  $("vidModel").addEventListener("change", () => {
    const m = $("vidModel").value;
    if (m === "ltx-video") {
      $("vidSteps").value = 28;
      $("vidGuidance").value = 3.5;
      $("i2vUploadField").classList.remove("hidden");
      $("vidModelHint").textContent = "LTX-Video 2B：T4 16GB 即可流畅运行，支持文生视频 + 图生视频，最省计算单元。";
    } else if (m === "wan2.1-1.3b") {
      $("vidSteps").value = 30;
      $("vidGuidance").value = 5.0;
      $("i2vUploadField").classList.add("hidden");
      $("vidModelHint").textContent = "阿里 Wan 2.1 1.3B：轻量高质文生视频模型，T4 / L4 均可运行。";
    } else {
      $("vidSteps").value = 35;
      $("vidGuidance").value = 5.0;
      $("i2vUploadField").classList.remove("hidden");
      $("vidModelHint").textContent = "阿里 Wan 2.2 TI2V-5B：画质出众，支持文生+图生视频，推荐在 L4 (24GB) 或 A100 上运行。";
    }
  });

  // I2V image upload
  $("i2vDropZone").addEventListener("click", (e) => {
    if (e.target.id === "clearI2vBtn") return;
    $("i2vFileInput").click();
  });

  $("i2vFileInput").addEventListener("change", () => {
    const file = $("i2vFileInput").files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      state.i2vBase64 = dataUrl.includes(",") ? dataUrl.split(",")[1] : dataUrl;
      state.i2vGalleryId = null;
      showI2vPreview(dataUrl);
    };
    reader.readAsDataURL(file);
  });

  function showI2vPreview(src) {
    $("i2vPreviewImg").src = src;
    $("i2vEmptyHint").classList.add("hidden");
    $("i2vPreviewBox").classList.remove("hidden");
  }

  $("clearI2vBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    state.i2vBase64 = null;
    state.i2vGalleryId = null;
    $("i2vFileInput").value = "";
    $("i2vPreviewBox").classList.add("hidden");
    $("i2vEmptyHint").classList.remove("hidden");
  });

  function useGalleryItemForVideo(item) {
    state.i2vBase64 = null;
    state.i2vGalleryId = item.id;
    showI2vPreview(item.local_url);
    if ($("vidModel").value === "wan2.1-1.3b") {
      $("vidModel").value = "ltx-video";
      $("vidModel").dispatchEvent(new Event("change"));
    }
    if (!$("vidPrompt").value.trim() && item.prompt) {
      $("vidPrompt").value = `${item.prompt}, smooth cinematic camera motion`;
    }
    document.querySelector('.tab[data-tab="video"]').click();
    toast("已载入参考图，现在可直接生成图生视频！", "success");
  }

  $("sendImgToVideoBtn").addEventListener("click", () => {
    if (state.lastImageItem) useGalleryItemForVideo(state.lastImageItem);
  });

  // Status & Telemetry polling
  async function refreshStatus() {
    try {
      const res = await api("/local/status");
      state.connected = Boolean(res.connected);
      state.health = res.health;
      state.config = res.config || {};

      const pill = $("statusPill");
      if (state.connected && state.health) {
        const h = state.health;
        pill.classList.add("online");
        pill.classList.remove("offline");
        $("statusText").textContent = h.drive_mounted ? "已连接 · Drive缓存就绪" : "已连接 Colab";
        $("gpuValue").textContent = `${h.gpu_name} (${h.vram_used_gb}/${h.vram_total_gb}GB)`;
        $("unitValue").textContent = `~${Number(h.est_cu_used || 0).toFixed(2)} CU`;
        $("uptimeValue").textContent = formatDuration(h.uptime_seconds);
        $("shutdownBtn").disabled = false;

        const alertMin = Number(state.config.idle_alert_minutes || 10);
        const idleMin = Math.floor((h.idle_seconds || 0) / 60);
        if (!h.active_job_id && idleMin >= alertMin && !state.idleDismissed) {
          $("idleMinutesText").textContent = String(idleMin);
          $("idleBanner").classList.remove("hidden");
        } else if (h.active_job_id || idleMin < alertMin) {
          $("idleBanner").classList.add("hidden");
          state.idleDismissed = false;
        }
      } else {
        pill.classList.remove("online");
        pill.classList.add("offline");
        $("statusText").textContent = res.configured ? "Colab 未响应 / 已断开" : "未连接 Colab";
        $("gpuValue").textContent = "--";
        $("unitValue").textContent = "0.00 CU";
        $("uptimeValue").textContent = "--";
        $("shutdownBtn").disabled = true;
        $("idleBanner").classList.add("hidden");
      }
    } catch (_) {
      // ignore background poll error
    }
  }

  // Modal controls
  function openConnectModal() {
    $("connectCodeInput").value = "";
    $("colabUrlInput").value = state.config.colab_url || "";
    $("colabTokenInput").value = state.config.colab_token || "";
    $("idleAlertInput").value = state.config.idle_alert_minutes || 10;
    $("connectModal").classList.remove("hidden");
  }
  function closeConnectModal() {
    $("connectModal").classList.add("hidden");
  }

  $("connectModalBtn").addEventListener("click", openConnectModal);
  $("closeModalBtn").addEventListener("click", closeConnectModal);
  $("cancelModalBtn").addEventListener("click", closeConnectModal);

  $("saveConnectBtn").addEventListener("click", async () => {
    try {
      await api("/local/config", {
        method: "POST",
        body: JSON.stringify({
          connect_code: $("connectCodeInput").value.trim(),
          colab_url: $("colabUrlInput").value.trim(),
          colab_token: $("colabTokenInput").value.trim(),
          idle_alert_minutes: Number($("idleAlertInput").value || 10),
        }),
      });
      closeConnectModal();
      await refreshStatus();
      if (state.connected) {
        toast(`连接成功！当前显卡：${state.health.gpu_name}`, "success");
      } else {
        toast("配置已保存，但暂未检测到在线的 Colab 服务，请确认 Notebook 正在运行。", "error");
      }
    } catch (e) {
      toast(e.message, "error");
    }
  });

  // Remote unload & shutdown
  async function handleUnload() {
    if (!state.connected) return toast("尚未连接 Colab", "error");
    try {
      await api("/local/unload", { method: "POST" });
      toast("已清理云端 GPU 显存缓存", "success");
      refreshStatus();
    } catch (e) {
      toast(e.message, "error");
    }
  }
  $("unloadGpuBtn1").addEventListener("click", handleUnload);
  $("unloadGpuBtn2").addEventListener("click", handleUnload);

  async function handleShutdown() {
    if (!confirm("确定立即远程断开并删除 Colab 运行时吗？\n\n这将立刻停止消耗你的计算单元（已生成的作品已保存在本地）。")) {
      return;
    }
    try {
      await api("/local/shutdown", { method: "POST" });
      toast("已发送释放指令，Colab 运行时将在 2 秒内彻底关闭并停止计费！", "success");
      $("idleBanner").classList.add("hidden");
      setTimeout(refreshStatus, 2500);
    } catch (e) {
      toast(e.message, "error");
    }
  }
  $("shutdownBtn").addEventListener("click", handleShutdown);
  $("idleShutdownBtn").addEventListener("click", handleShutdown);
  $("dismissIdleBtn").addEventListener("click", () => {
    state.idleDismissed = true;
    $("idleBanner").classList.add("hidden");
  });

  // Job polling helper
  async function pollJob(jobId, kind) {
    const isImg = kind === "image";
    const progressBox = $(isImg ? "imgProgressBox" : "vidProgressBox");
    const stageEl = $(isImg ? "imgStageText" : "vidStageText");
    const pctEl = $(isImg ? "imgPctText" : "vidPctText");
    const fillEl = $(isImg ? "imgProgressFill" : "vidProgressFill");
    const btn = $(isImg ? "generateImgBtn" : "generateVidBtn");

    progressBox.classList.remove("hidden");
    btn.disabled = true;

    while (true) {
      await new Promise((r) => setTimeout(r, 1500));
      const job = await api(`/local/jobs/${jobId}`);
      const pct = Math.min(100, Math.max(0, Number(job.progress || 0)));
      stageEl.textContent = job.stage || job.status;
      pctEl.textContent = `${pct}%`;
      fillEl.style.width = `${pct}%`;

      if (job.status === "completed") {
        btn.disabled = false;
        if (job.download_error) {
          throw new Error(job.download_error);
        }
        return job;
      }
      if (job.status === "failed") {
        btn.disabled = false;
        throw new Error(job.error || "云端推理失败");
      }
    }
  }

  // Generate Image
  $("generateImgBtn").addEventListener("click", async () => {
    if (!state.connected) {
      openConnectModal();
      return toast("请先连接你的 Colab 云端引擎", "error");
    }
    const prompt = $("imgPrompt").value.trim();
    if (!prompt) return toast("请输入图片提示词", "error");

    const [width, height] = $("imgSize").value.split("x").map(Number);
    try {
      const start = await api("/local/generate", {
        method: "POST",
        body: JSON.stringify({
          task: "image",
          model_id: $("imgModel").value,
          prompt,
          negative_prompt: $("imgNegative").value.trim(),
          width,
          height,
          steps: Number($("imgSteps").value || 4),
          guidance_scale: Number($("imgGuidance").value || 0),
          seed: Number($("imgSeed").value ?? -1),
        }),
      });
      const done = await pollJob(start.job_id, "image");
      const item = done.local_item;
      state.lastImageItem = item;
      $("imgPreviewStage").innerHTML = `<img src="${item.local_url}" alt="Generated image" />`;
      $("downloadImgLink").href = item.local_url;
      $("downloadImgLink").download = item.filename;
      $("imgResultActions").classList.remove("hidden");
      $("imgMetaFooter").classList.remove("hidden");
      $("imgMetaFooter").textContent = `模型: ${item.model_id} · 耗时: ${done.elapsed_seconds}s · 种子: ${item.seed} · 已存本地: outputs/${item.filename}`;
      toast("图片生成完成并已保存到本地！", "success");
      loadGallery();
      refreshStatus();
    } catch (e) {
      $("generateImgBtn").disabled = false;
      toast(e.message, "error");
    }
  });

  // Generate Video
  $("generateVidBtn").addEventListener("click", async () => {
    if (!state.connected) {
      openConnectModal();
      return toast("请先连接你的 Colab 云端引擎", "error");
    }
    const prompt = $("vidPrompt").value.trim();
    if (!prompt) return toast("请输入视频提示词", "error");

    const [width, height] = $("vidSize").value.split("x").map(Number);
    const modelId = $("vidModel").value;
    if (modelId === "wan2.2-5b" && state.health && state.health.vram_total_gb < 20) {
      if (!confirm("当前 Colab 分配的是 16GB 显存显卡（如 T4），跑 Wan 2.2 5B 可能显存不足或极慢。仍要尝试吗？\n\n（推荐改选 LTX-Video 2B 或 Wan 2.1 1.3B）")) {
        return;
      }
    }

    try {
      const payload = {
        task: "video",
        model_id: modelId,
        prompt,
        negative_prompt: $("vidNegative").value.trim(),
        width,
        height,
        num_frames: Number($("vidFrames").value || 49),
        fps: modelId === "ltx-video" ? 24 : 16,
        steps: Number($("vidSteps").value || 28),
        guidance_scale: Number($("vidGuidance").value || 3.5),
        seed: Number($("vidSeed").value ?? -1),
      };
      if (modelId !== "wan2.1-1.3b") {
        if (state.i2vBase64) payload.init_image_base64 = state.i2vBase64;
        if (state.i2vGalleryId) payload.source_gallery_id = state.i2vGalleryId;
      }

      const start = await api("/local/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const done = await pollJob(start.job_id, "video");
      const item = done.local_item;
      $("vidPreviewStage").innerHTML = `<video src="${item.local_url}" controls autoplay loop playsinline></video>`;
      $("downloadVidLink").href = item.local_url;
      $("downloadVidLink").download = item.filename;
      $("vidResultActions").classList.remove("hidden");
      $("vidMetaFooter").classList.remove("hidden");
      $("vidMetaFooter").textContent = `模型: ${item.model_id} · 耗时: ${done.elapsed_seconds}s · 种子: ${item.seed} · 已存本地: outputs/${item.filename}`;
      toast("视频生成完成并已保存到本地！", "success");
      loadGallery();
      refreshStatus();
    } catch (e) {
      $("generateVidBtn").disabled = false;
      toast(e.message, "error");
    }
  });

  // MiniMax-H3 ComfyUI launcher
  $("saveComfyUrlBtn").addEventListener("click", async () => {
    const url = $("comfyUrlInput").value.trim();
    await api("/local/config", { method: "POST", body: JSON.stringify({ comfyui_url: url }) });
    toast("已保存 MiniMax-H3 ComfyUI 链接", "success");
  });
  $("openComfyBtn").addEventListener("click", () => {
    const url = $("comfyUrlInput").value.trim();
    if (!url) return toast("请先粘贴 ComfyUI 的临时隧道链接", "error");
    window.open(url, "_blank");
  });

  // Finder shortcuts
  async function openFolder(target) {
    try {
      await api("/local/open-folder", { method: "POST", body: JSON.stringify({ target }) });
    } catch (e) {
      toast(e.message, "error");
    }
  }
  $("openOutputsFolderBtn").addEventListener("click", () => openFolder("outputs"));
  $("openColabFolderBtn").addEventListener("click", () => openFolder("colab"));
  $("guideOpenColabBtn").addEventListener("click", () => openFolder("colab"));

  // Local Gallery
  async function loadGallery() {
    try {
      const data = await api("/local/gallery");
      const items = data.items || [];
      $("galleryCount").textContent = String(items.length);
      const grid = $("galleryGrid");

      if (!items.length) {
        grid.innerHTML = `
          <div class="empty-state" style="grid-column:1/-1;">
            <div class="empty-icon">📁</div>
            <p>还没有本地作品，去生成第一张图片或视频吧！</p>
          </div>`;
        return;
      }

      grid.innerHTML = "";
      for (const item of items) {
        const card = document.createElement("div");
        card.className = "gallery-item";
        const isVideo = item.task === "video" || item.filename.endsWith(".mp4");
        const mediaHtml = isVideo
          ? `<video src="${item.local_url}" controls muted loop playsinline></video>`
          : `<img src="${item.local_url}" alt="Generated media" loading="lazy" />`;

        card.innerHTML = `
          <div class="gallery-media">${mediaHtml}</div>
          <div class="gallery-info">
            <div class="gallery-prompt" title="${(item.prompt || "").replace(/"/g, "&quot;")}">${item.prompt || "无提示词"}</div>
            <div class="gallery-meta">
              <span>${item.model_id || (isVideo ? "video" : "image")}</span>
              <span>${(item.created_at || "").replace("T", " ").slice(5, 16)}</span>
            </div>
            <div class="gallery-actions">
              ${!isVideo ? `<button class="btn btn-accent btn-xs i2v-btn">转图生视频</button>` : ""}
              <a class="btn btn-secondary btn-xs" href="${item.local_url}" download="${item.filename}">下载</a>
              <button class="btn btn-ghost btn-xs del-btn">删除</button>
            </div>
          </div>`;

        const i2vBtn = card.querySelector(".i2v-btn");
        if (i2vBtn) i2vBtn.addEventListener("click", () => useGalleryItemForVideo(item));
        card.querySelector(".del-btn").addEventListener("click", async () => {
          if (!confirm("确定从本地删除该文件吗？")) return;
          await api(`/local/gallery/${item.id}`, { method: "DELETE" });
          loadGallery();
        });
        grid.appendChild(card);
      }
    } catch (e) {
      toast(e.message, "error");
    }
  }

  $("refreshGalleryBtn").addEventListener("click", loadGallery);

  // Init
  async function init() {
    try {
      const cfg = await api("/local/config");
      state.config = cfg;
      $("comfyUrlInput").value = cfg.comfyui_url || "";
    } catch (_) {}
    await Promise.all([refreshStatus(), loadGallery()]);
    setInterval(refreshStatus, 8000);
  }

  init();
})();
