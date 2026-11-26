# Colab Media Studio — 本地 App + Google Colab 云端显卡创作台

让你像使用自己本地显卡一样，在 Mac 桌面界面里直接调用 Google One / Google AI Pro 赠送的 Colab 计算单元（200 CU/月），完成 **AI 生图**、**文生视频**、**图生视频**，并提供 **MiniMax-H3（海螺 3 开源版）** 专属运行方案与全套**省计算单元守护机制**。

---

## 目录结构

- `启动 Colab Media Studio.command` — **双击即可启动**本地 Mac 创作台（零依赖，直接调用系统 Python 3）
- `local_app/` — 本地 Web App 服务端与前端界面（自动下载作品到 `outputs/`，支持离线浏览作品库）
- `colab/Colab_Media_Studio_Server.ipynb` — **主力云端引擎 Notebook**（集成 FLUX.1-schnell、SDXL、LTX-Video、阿里 Wan 2.1/2.2，支持 T4 / L4 / A100 自动适配、Google Drive 模型缓存、远程一键释放算力）
- `colab/Colab_MiniMax_H3_ComfyUI.ipynb` — **MiniMax-H3（海螺 3 · 33B 音视频）专用 ComfyUI Notebook**（推荐在抢到 A100 或 L4 高 RAM 时使用）
- `outputs/` — 本地永久保存的生成图片、视频和元数据记录

---

## 快速上手（3 步）

### 第一步：启动本地 Mac App
双击根目录的 **`启动 Colab Media Studio.command`**（或在终端运行 `python3 local_app/server.py`），浏览器会自动打开 `http://127.0.0.1:8765`。

### 第二步：在 Google Colab 启动云端 GPU 引擎
1. 打开 [Google Colab](https://colab.research.google.com/)，上传 `colab/Colab_Media_Studio_Server.ipynb`。
2. 在 Colab 顶部菜单选择 **代码执行程序 → 更改运行时类型**：
   - **T4（16GB，约 1.2 单元/小时）**：测试流程、文生图（FLUX / SDXL）、轻量生视频（LTX-Video 2B / Wan 2.1 1.3B）最省单元。
   - **L4（24GB，约 2.2 单元/小时）**：**性价比之王**，命中率远高于 A100，可流畅跑 **Wan 2.2 TI2V-5B** 高画质视频。
   - **A100（40GB/80GB，约 6–10 单元/小时）**：高配冲刺或运行 **MiniMax-H3** 时选用。
3. 按顺序运行 Notebook 单元格（首次会请求授权挂载 Google Drive，用于把大权重缓存在你的 Google One 空间里）。
4. 运行完成后，复制底部打印的一行 **一键连接码**（格式：`https://xxxx.trycloudflare.com::你的密钥`）。

### 第三步：连接并开始创作
1. 在本地 App 右上角点击 **连接设置**，粘贴刚才的「一键连接码」并保存。
2. 看到状态变绿并显示 GPU 型号后，即可直接生图、生视频、图生视频。
3. **用完务必点击右上角红色「释放算力」按钮**：会远程调用 `google.colab.runtime.unassign()` 立即销毁 Colab 运行时，防止空闲扣费！

---

## 关于 MiniMax-H3（海螺 3 开源版）

MiniMax-H3 是 33B 参数的全模态音视频生成模型（原生同步生成视频 + 立体声），对显存和系统内存要求非常高：

| Colab 显卡 | 能否运行 MiniMax-H3 | 说明 |
| :--- | :--- | :--- |
| **T4 (16GB)** | ❌ 不建议 / 会 OOM | 显存与内存均不足，建议用主引擎跑 FLUX + LTX-Video / Wan 2.1 |
| **L4 (24GB + 高 RAM)** | ⚠️ 勉强可跑（较慢） | 需使用 ComfyUI + Q4_K GGUF 量化 + 动态显存卸载 |
| **A100 (40GB/80GB + 高 RAM)** | ✅ 推荐 | 使用 `colab/Colab_MiniMax_H3_ComfyUI.ipynb` 可稳定运行 |

---

## 省计算单元最佳实践

1. **用完立刻释放**：不要只关浏览器标签页，必须点本地 App 的 **「释放算力」** 或 Colab 的 **「断开连接并删除运行时」**。
2. **善用 Google Drive 缓存**：首次下载模型耗时较长，挂载 Drive 后模型会持久化保存在 `MyDrive/ColabMediaStudio/`，下次启动无需重复下载。
3. **先图后视频，小参数试错**：先用 `FLUX.1-schnell`（4 步秒出图）敲定构图，再点 **「一键转图生视频」**，先用 `25 帧 / 480p` 验证动态效果，满意再拉高帧数。
4. **开启空闲提醒**：本地 App 默认在 Colab 空闲 10 分钟后弹出醒目提醒，防止忘记关机。
