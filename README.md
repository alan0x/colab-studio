# Colab Canvas Studio — 零网页 AI 无限画布本地创作台

无需打开 Google Colab 网页！基于 Google 官方 [`google-colab-cli`](https://pypi.org/project/google-colab-cli/)，让你在 Mac 本地打开一个 **LibTV 风格的无限画布创作台**，直接拖入参考素材、输入提示词、查看实时进度条、在画布上预览与编排生成的图片和视频，并实时监控当前云端显卡、Google 账号剩余计算单元与可用时长。

---

## 核心体验

1. **完全免开 Colab 网页**：首次在 App 内通过 Google 官方 OAuth 登录一次（凭证安全保存在 macOS 钥匙串），之后打开 App 即可直接使用你的 Google One / Google AI Pro 赠送的 Colab 算力。
2. **LibTV 风格无限画布**：
   - 自由拖拽、缩放（25%–200%）、抓手平移、一键整齐排版。
   - **直接拖入本地图片到画布**作为参考素材，选中任意图片点击 **「以此图生视频」**，生成的视频卡片会通过发光贝塞尔曲线与原图自动连线。
   - 生成时画布上即时弹出**实时进度卡片**，完成后自动下载成品到 `outputs/` 并无缝切换为高清图片/可播放视频。
3. **Google Drive 自动云端同步（充分利用 Google One 空间）**：
   - 直接复用同一次 Google OAuth 授权（自带 `drive.file` 权限，**无需额外登录**）。
   - 所有导入的参考素材自动备份到 `Google Drive / ColabCanvasStudio/materials/`，所有生成的图片和视频自动上传到 `ColabCanvasStudio/outputs/`，画布坐标与连线关系自动同步到 `canvas.json`。
   - 支持顶部 **☁️ Drive 云盘** 一键双向同步/恢复，以及在卡片与预览页直达 Google Drive 查看。
4. **实时显卡与算力余额 HUD**：
   - 顶部悬浮条实时显示：**当前分配的显卡与显存**、**账号真实剩余计算单元（CU）**、**按当前费率预计还可使用多少小时**。
   - **智能省单元守护**：支持待机按需自动开机 + **空闲自动释放显卡**（默认 10 分钟空闲自动执行 `colab stop`），也可随时一键手动释放，彻底杜绝忘关机扣费。
4. **全场景模型支持**：
   - **生图**：`FLUX.1-schnell`（4 步极速高清）、`SDXL 1.0 Base`
   - **生视频 / 图生视频**：`LTX-Video 2B`（T4/L4/A100 极速省单元）、`阿里 Wan 2.2 TI2V-5B`（L4/A100 电影级画质）、`阿里 Wan 2.1 1.3B`
   - **MiniMax-H3（海螺 3 · 33B 音视频）**：提供独立 [colab/Colab_MiniMax_H3_ComfyUI.ipynb](colab/Colab_MiniMax_H3_ComfyUI.ipynb)（建议抢到 A100 40GB/80GB 时使用）

---

## 快速启动

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
