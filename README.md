# Comfy-FlashVSR-Trunk ⚡

把 **FlashVSR 长视频 4x 超分流水线** 封装成一个开箱即用的 ComfyUI 扩展插件。  
一句话：你不必再手跑 6 份分块工作流 + 合并脚本——丢一个视频进去，插件自动  
「时间分块 → 逐块重叠上采样 → 合并去重 + 复用音频」出最终视频。

> 本插件是 **FlashVSR 流水线的「主干（Trunk）」编排层**，模型与推理代码复用已安装的  
> [`ComfyUI-FlashVSR`](https://github.com/1038lab/ComfyUI-FlashVSR)。



---

## 为什么需要它（解决的问题）

原生 `AILab_FlashVSR` 在整段长视频 4x 时，会在 CPU 上一次性分配  
`torch.zeros((nf, oh, ow, C))` 整段画布——例如 759 帧 4x ≈ **60.9 GB**，直接 OOM  
（这正是之前 `AILab_FlashVSR.py:399` 的崩溃点）。

本插件的解法：**时间分块 + 相邻重叠 + 合并时裁掉重叠区**。

- 每块只分配「单块画布」（如 128 帧 4x ≈ 10 GB），不再炸内存；
- 相邻块重叠若干帧，合并时丢弃重叠区，边界落在重叠内 → 时域连续、无可见接缝；
- **文件流水线模式**还会把每块结果立刻落盘再合并，连「返回整段张量」这一步的  
  60 GB 内存压力也规避掉，对极长视频完全 RAM-safe。

---

## 安装

### 方式 A：ComfyUI Manager 一键安装（推荐）

ComfyUI Manager → **Install Custom Nodes** → **Install via Git URL** → 粘贴本仓库地址：

```
https://gitee.com/simino/Comfy-FlashVSR-Trunk
# 或 https://github.com/yisino/Comfy-FlashVSR-Trunk
# 或 https://codeup.aliyun.com/5f28c467769820a3e817fc05/yisino/Comfy-FlashVSR-Trunk
```

Manager 会克隆本仓库、自动执行 `install.py`（`install.py` 会装好本插件依赖，并**自动克隆 peer 依赖 `ComfyUI-FlashVSR`** 到同级 `custom_nodes/`，跳过 torch 等 ComfyUI 自带项）。

> 想让节点出现在 Manager 的「可搜索列表」中（而非仅 Git URL 安装），需把本仓库提交到
> ComfyUI Manager 的 custom-node-list（提 PR 到 `ComfyUI-Manager/custom-node-list`），
> 或在本仓库根目录放置符合规范的 `node.json`（已带）。

### 方式 B：手动 git clone

```bash
cd /path/to/ComfyUI/custom_nodes
git clone <本仓库地址> Comfy-FlashVSR-Trunk
# 安装依赖（可选，install.py 已自动处理）
#   cd Comfy-FlashVSR-Trunk && pip install -r requirements.txt
#   若未用 Manager，请手动安装 peer 依赖：
#   git clone https://github.com/1038lab/ComfyUI-FlashVSR ../ComfyUI-FlashVSR
```

### 依赖说明

| 依赖 | 类型 | 说明 |
|------|------|------|
| `imageio-ffmpeg` | pip（本插件） | 分块视频合并复用其内置 ffmpeg（与 VHS 一致） |
| `ComfyUI-FlashVSR` | peer（custom node） | **复用其模型与推理代码**；`install.py` 自动克隆，无需手动 |
| `torch` / `numpy` | ComfyUI 自带 | 不重装，避免破坏 ComfyUI venv |

安装后 **重启 ComfyUI**，节点会出现在 `🧪AILab/⚡FlashVSR/Trunk` 分类下。

### 仓库地址（多平台镜像）

| 平台          | 地址                                                                        |
| ----------- | ------------------------------------------------------------------------- |
| Gitee       | `git@gitee.com:simino/Comfy-FlashVSR-Trunk.git`                           |
| GitHub      | `git@github.com:yisino/Comfy-FlashVSR-Trunk.git`                          |
| Codeup（阿里云） | `git@codeup.aliyun.com:5f28c467769820a3e817fc05/yisino/Comfy-FlashVSR-Trunk.git` |

> Gitee 命名空间默认 `simino`（本机 `id_ed25519` 已授权），如需改命名空间改 `publish.ps1` / `publish.sh` 里的 URL 即可。

---

## 四个节点

| 节点                                            | 用途                                       | 何时用                     |
| --------------------------------------------- | ---------------------------------------- | ----------------------- |
| **FlashVSR Trunk ⚡ (File Pipeline)**          | 给源视频**路径**，一键出最终 mp4                     | ✅ 长视频首选（RAM-safe）       |
| **FlashVSR Trunk ⚡ Advanced (File Pipeline)** | 同上，但暴露高级模型参数                             | 需要调模型版本 / tiling / 质量   |
| **FlashVSR Trunk ⚡ Frames (Drop-in)**         | `IMAGE→IMAGE`，可直接**替换** `AILab_FlashVSR` | 短片段、或沿用现有 VHS 图         |
| **FlashVSR Trunk ⚡ Merge Chunks**             | 合并已有的分块视频文件（重叠去重 + 复用源音频）                | 对应历史 `merge_overlap.py` |

---

## 快速上手

### 1) 文件流水线（推荐，最简单）

新建图：`Load Image/Video 路径` → 其实不需要 → 直接拖入 **FlashVSR Trunk ⚡ (File Pipeline)**，  
填 `src_video` 为视频绝对路径（如 `D:/videos/in.mp4`），其余默认即可。

- `chunk_size`：每块帧数，4x 建议 **≤160**（越大越快越占内存）。
- `overlap`：相邻块重叠帧数，默认 16（越大接缝越平滑但越慢）。
- 运行后返回最终 mp4 路径 + 预览帧。

### 2) 即插即用（替换原节点）

打开 `example_workflows/FlashVSR_Trunk_demo.json`：  
`VHS_LoadVideo → FlashVSR Trunk ⚡ Frames → VHS_VideoCombine`，  
与原始 `FlashVSR.json` 结构一致，只是把 `AILab_FlashVSR` 换成了 `FlashVSR_Trunk_Frames`。

### 3) 仅合并分块

如果你仍用历史 6 段工作流产出 `FlashVSR_ovchunk*.mp4`，可直接用  
**FlashVSR Trunk ⚡ Merge Chunks** 节点合并（或 `legacy/scripts/merge_overlap.py`）。

---

## 参数建议

| 环境                           | chunk_size     | overlap | 说明              |
| ---------------------------- | -------------- | ------- | --------------- |
| RTX 3080 / 20GB 显存 / 32GB 内存 | 128            | 16      | 4x 单块画布≈10GB，安全 |
| 更长 / 更低内存                    | 96             | 12      | 更保守             |
| 短片段（<300 帧）                  | 直接用 Drop-in 节点 | 16      | 无需文件流水线         |

---

## 目录结构

```
Comfy-FlashVSR-Trunk/
├── __init__.py              # 节点注册
├── nodes.py                 # 4 个节点定义
├── trunk_core.py            # 核心：分块规划 / FlashVSR 调用 / ffmpeg 合并与视频 IO
├── requirements.txt         # 本插件 pip 依赖（imageio-ffmpeg）
├── install.py               # ComfyUI Manager 安装钩子：装依赖 + 自动克隆 peer(ComfyUI-FlashVSR)
├── node.json                # ComfyUI Manager 元数据
├── README.md
├── LICENSE                  # MIT
├── publish.ps1 / publish.sh # 一键多平台同步（含 -Deploy 部署到本地 ComfyUI）
├── example_workflows/
│   └── FlashVSR_Trunk_demo.json   # 即插即用示例（VHS → Trunk_Frames → VHS）
└── legacy/                  # 前一个任务的全部修改（保留可追溯）
    ├── workflows/           # FlashVSR.json(改unload) + 6 份重叠分块工作流
    └── scripts/             # merge_overlap / queue_overlap / watch_and_merge / 生成器
```

---

## 多平台发布 / 同步

仓库同时镜像到 **Gitee / GitHub / Codeup（阿里云）** 三个平台。  
本机已附带一键脚本，在你**正常终端**（SSH agent 已加载密钥、known_hosts 就绪）运行：

```powershell
# Windows (PowerShell)
.\publish.ps1            # 推送 gitlab + github + codeup（所有分支 + 标签）
.\publish.ps1 -Deploy    # 推送 + 同步到本地 ComfyUI custom_nodes（保持源/部署一致）
```

```bash
# Linux / macOS / Git Bash
./publish.sh             # 推送三平台
./publish.sh --deploy    # 推送 + 同步到本地 ComfyUI
```

> 首次发布前，请先在三个平台的 Web 界面各建一个**空仓库**（同名 `Comfy-FlashVSR-Trunk`），  
> 然后跑上面的脚本即可。分组/命名空间在脚本顶部的 `$remotes` / 变量里改。

---

## 卸载

直接删除 `custom_nodes/Comfy-FlashVSR-Trunk` 文件夹，重启 ComfyUI 即可，无残留。

---

## 已知限制

- 文件流水线模式需要 ComfyUI 环境能写文件到 `src_video` 同目录（或你指定的 `output_dir`）。
- `FlashVSR Trunk ⚡ Frames` 会返回整段上采样张量；**极长 4x 视频**请改用文件流水线模式。
- 首次运行仍需 `ComfyUI-FlashVSR` 自动下载模型权重（与直接用 FlashVSR 一致）。

---

*Trunk = 把「能跑通长视频的 FlashVSR 流水线」做成一条主干，让你只关心输入和输出。*
