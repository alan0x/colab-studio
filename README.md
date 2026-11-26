# Colab Agent Studio — 云端多模态 AI Agent & 无限画布桌面端 (v2.0)

无需打开 Google Colab 网页！基于 Google 官方 [`google-colab-cli`](https://pypi.org/project/google-colab-cli/)，把你的 Google One / Google AI Pro 赠送的 200 计算单元，变成一个集 **「类 ChatGPT / Claude / Antigravity 全能多模态 Agent」** 与 **「LibTV 风格 AI 无限画布」** 于一体的 macOS & Windows 独立桌面应用。

---

## 核心能力

1. **💬 全能多模态 AI Agent 工作台（直跑 `Qwen3.8-27B` 等开源大模型）**：
   - 在 Colab GPU 上自动启动预编译 CUDA `llama-server`，支持 **`Qwen3.8-27B`**（L4/A100 旗舰全模态 + T4 Q2 轻量版）、**`Qwen3.5-9B`** 与 **`Qwen2.5-VL-7B`**。
   - 原生支持**发图识图、上传代码/文档、深度思考链折叠、流式打字机输出与多会话历史管理**。
   - 内置四大 Agent 工具套件：
     - 🎨 **联动无限画布**：自然语言指挥 Agent 构思提示词、调用生图/图生视频并自动落入画布与 Google Drive（单卡自动显存热切换）。
     - 🌐 **实时联网搜索与网页正文抓取**。
     - 💻 **本地工作区文件与终端（类似 Claude Code / Antigravity）**：读写本地项目文件、运行 Python/Shell 脚本（默认带安全确认卡片，且严格限定在工作区目录内）。
2. **🎨 LibTV 风格无限画布（Media Studio）**：
   - 自由拖拽、缩放（25%–200%）、抓手平移、一键整齐排版。
   - **直接拖入本地图片到画布**作为参考素材，选中任意图片点击 **「以此图生视频」**，生成的视频卡片会通过发光贝塞尔曲线与原图自动连线。
   - 支持 `RealVisXL V5.0`、`Animagine XL 4.0`、`阿里 Z-Image Turbo`、`Shuttle 3.1`、`LTX-Video 2B`、`阿里 Wan 2.2 TI2V-5B`、`Wan 2.1 1.3B`。
3. **☁️ Google Drive 自动云端同步（充分利用 Google One 空间）**：
   - 直接复用同一次 Google OAuth 授权（自带 `drive.file` 权限，**无需额外登录**）。
   - 所有参考素材、生成图片/视频及画布布局自动备份到 `Google Drive / ColabCanvasStudio/`，支持一键双向恢复。
4. **⚡ 实时显卡 & 算力余额 HUD（省单元守护）**：
   - 顶部悬浮条实时显示当前显卡、当前大脑模型、账号真实剩余计算单元（CU）与预计可用小时数；支持默认 10 分钟空闲自动关机与一键释放算力。

---

## 下载安装（macOS & Windows 独立桌面 App）

前往 [GitHub Releases](https://github.com/alan0x/colab-media-studio/releases/latest) 下载对应系统的安装包：

### Windows 10 / 11（免配环境 · 开箱即用）
- **推荐安装版**：下载 `Colab-Canvas-Studio-v1.0.0-Windows-x64-Setup.exe`，双击一路下一步即可安装（内置 Python、WebView2 桌面窗口与 `google-colab-cli`，**无需安装 Python 或命令行工具**）。
- **绿色便携版**：下载 `Colab-Canvas-Studio-v1.0.0-Windows-x64-Portable.zip`，解压后直接运行 `Colab Canvas Studio.exe`。
- 打开后点击右上角 **「登录 Google 账号」** 完成一次授权即可开始创作，所有作品默认保存在 `文档\ColabCanvasStudio\outputs` 并自动同步至 Google Drive。

### macOS（Apple Silicon）
下载 `Colab-Canvas-Studio-v1.0.0-macOS-arm64.dmg`：

1. 打开 `.dmg`，将 **Colab Canvas Studio.app** 拖入 **Applications** 文件夹。
2. 安装 Google 官方 Colab CLI（仅需一次）：
   ```bash
   uv tool install google-colab-cli
   # 或 pip3 install google-colab-cli
   ```
3. 如首次打开时 macOS 提示“无法验证开发者”（开源自签名应用），在终端执行一次放行命令即可：
   ```bash
   xattr -cr "/Applications/Colab Canvas Studio.app"
   ```
4. 打开 **Colab Canvas Studio**，点击右上角 **「登录 Google 账号」** 完成一次授权，即可直接拖入素材、输入提示词生成图片与视频！

### 从源码自行构建 macOS `.app` 和 `.dmg`
```bash
./scripts/build_mac_app.sh 1.0.0
```

---

## 快速启动（开发模式）

1. 双击根目录的 **`启动 Colab Media Studio.command`**（或在终端运行 `python3 local_app/server.py`）。
   - 启动器会自动检测并安装 `google-colab-cli`。
2. 首次打开点击右上角 **「登录 Google 账号」**，按弹窗 2 步完成 Google 官方授权即可永久记住登录态。
3. 拖入素材或输入提示词，点击右下角 **「✦ 生成」**！系统会自动为你申请所选显卡（L4 推荐 / T4 省单元 / A100 高配，若缺货自动优雅降级），实时显示渲染进度并在画布中呈现成品。

---

## 目录说明

- `启动 Colab Media Studio.command` — macOS 一键启动器
- `local_app/server.py` — 本地无限画布后端、作品库与实时任务进度 API
- `local_app/colab_manager.py` — 基于 `google-colab-cli` 的全自动 OAuth、算力余额查询、GPU 启停、文件传输与空闲自动关机守护
- `local_app/remote_worker.py` — 远程常驻内核 GPU 推理脚本（同一次开机自动缓存已加载模型，二次生成秒开）
- `local_app/static/` — 无限画布前端界面（HTML / CSS / JS）
- `colab/` — 保留的独立 Colab Notebook（含 MiniMax-H3 ComfyUI 专属方案）
- `outputs/` — 本地离线保存的所有图片、视频与画布节点布局
