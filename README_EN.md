# Comfy-FlashVSR-Trunk ⚡

**Turn FlashVSR long-video 4x upscaling into a one-click ComfyUI pipeline.**

Instead of manually running 6 chunked workflows plus a merge script, drop in a
video and the plugin automatically does
**temporal chunking → overlapped per-chunk upscaling → merge & dedupe + audio reuse**.

> This plugin is the **orchestration "trunk"** on top of FlashVSR. It reuses the
> model and inference code from the already-installed
> [`ComfyUI-FlashVSR`](https://github.com/1038lab/ComfyUI-FlashVSR).

[中文说明 / Chinese version](README.md)

---

## Why it exists

The native `AILab_FlashVSR` node allocates one huge CPU canvas for the whole clip:
`torch.zeros((nf, oh, ow, C))`. For 759 frames at 4x that is ≈ **60.9 GB** —
instant OOM (this is the crash at `AILab_FlashVSR.py:399`).

**The fix: temporal chunks + neighbour overlap + trimming the overlap on merge**

- Each chunk only allocates its own canvas (e.g. 128 frames at 4x ≈ 10 GB);
- Adjacent chunks overlap by N frames; the overlap is discarded at merge so the
  seam stays inside it → temporally continuous, no visible seam;
- **File Pipeline mode** writes each chunk to disk immediately, so even the
  "return the whole tensor" step avoids the 60 GB spike — fully RAM-safe for
  very long clips.

---

## Install

### Option A — ComfyUI Manager (recommended)

ComfyUI Manager → **Install Custom Nodes** → **Install via Git URL** → paste:

```
https://gitee.com/simino/Comfy-FlashVSR-Trunk
# or https://github.com/yisino/Comfy-FlashVSR-Trunk
# or https://codeup.aliyun.com/5f28c467769820a3e817fc05/yisino/Comfy-FlashVSR-Trunk
```

Manager clones the repo and runs `install.py`, which installs the pip dependency
and **auto-clones the `ComfyUI-FlashVSR` peer** into the sibling `custom_nodes/`
directory (skipping torch etc. that ComfyUI already provides).

### Option B — Manual

```bash
cd /path/to/ComfyUI/custom_nodes
git clone <repo-url> Comfy-FlashVSR-Trunk
# optional: pip install -r Comfy-FlashVSR-Trunk/requirements.txt
# if you skip Manager, install the peer manually:
#   git clone https://github.com/1038lab/ComfyUI-FlashVSR ../ComfyUI-FlashVSR
```

### Dependencies

| Dependency | Kind | Notes |
|---|---|---|
| `imageio-ffmpeg` | pip (this plugin) | Provides the ffmpeg binary used for merging (same one VHS uses) |
| `ComfyUI-FlashVSR` | peer (custom node) | **Model + inference code reused**; auto-cloned by `install.py` |
| `torch` / `numpy` | ComfyUI runtime | Never reinstalled — avoids breaking the ComfyUI venv |

Then **restart ComfyUI**; the nodes appear under `🧪AILab/⚡FlashVSR/Trunk`.

### Mirrors

| Platform | URL |
|---|---|
| Gitee | `git@gitee.com:simino/Comfy-FlashVSR-Trunk.git` |
| GitHub | `git@github.com:yisino/Comfy-FlashVSR-Trunk.git` |
| Codeup (Alibaba Cloud) | `git@codeup.aliyun.com:5f28c467769820a3e817fc05/yisino/Comfy-FlashVSR-Trunk.git` |

---

## The four nodes

| Node | Purpose | When to use |
|---|---|---|
| **FlashVSR Trunk ⚡ (File Pipeline)** | Give it a **path**, get a final mp4 | ✅ Best for long videos (RAM-safe) |
| **FlashVSR Trunk ⚡ Advanced (File Pipeline)** | Same, with advanced model params | Tuning model version / tiling / quality |
| **FlashVSR Trunk ⚡ Frames (Drop-in)** | `IMAGE→IMAGE`, drop-in for `AILab_FlashVSR` | Short clips, or keep an existing VHS graph |
| **FlashVSR Trunk ⚡ Merge Chunks** | Merge existing chunk files (dedupe overlap + reuse audio) | Successor to the old `merge_overlap.py` |

---

## Quick start

### 1) File Pipeline (simplest)

Add **FlashVSR Trunk ⚡ (File Pipeline)**, set `src_video` to an absolute path
(e.g. `D:/videos/in.mp4`), leave the rest at defaults.

- `chunk_size`: frames per chunk; at 4x keep it **≤160** (larger = faster, more RAM).
- `overlap`: overlap frames between chunks, default 16 (larger = smoother seam, slower).
- Returns the final mp4 path + a preview frame.

### 2) Drop-in replacement

Open `example_workflows/FlashVSR_Trunk_demo.json`:
`VHS_LoadVideo → FlashVSR Trunk ⚡ Frames → VHS_VideoCombine` — same structure as
the original `FlashVSR.json`, just with `AILab_FlashVSR` swapped for `FlashVSR_Trunk_Frames`.

### 3) Merge chunks only

If you still produce `FlashVSR_ovchunk*.mp4` with the old 6-workflow setup, use
**FlashVSR Trunk ⚡ Merge Chunks** (or `legacy/scripts/merge_overlap.py`).

---

## Recommended settings

| Hardware | chunk_size | overlap | Note |
|---|---|---|---|
| RTX 3080 / 20 GB VRAM / 32 GB RAM | 128 | 16 | 4x single-chunk canvas ≈ 10 GB, safe |
| Longer clips / less RAM | 96 | 12 | More conservative |
| Short clips (<300 frames) | Use the Drop-in node | 16 | No need for the file pipeline |

---

## Testing

`tests/test_trunk.py` ships **42 tests** (node contracts / chunk planning / module
resolution / ffmpeg IO / end-to-end merge / tensor rebuild / param mapping /
error boundaries / type hints). **No GPU or real model inference required.**

```bash
python tests/test_trunk.py                # standalone runner, no pytest needed
python -m pytest tests/ -v                # or with pytest

# point it at a different ComfyUI install:
COMFY_CUSTOM_NODES=/path/to/ComfyUI/custom_nodes python tests/test_trunk.py
```

> Requires `ComfyUI-FlashVSR` (peer; test group C verifies it is discoverable) and
> `ffmpeg` (via `imageio-ffmpeg` or system). CI clones the peer and installs ffmpeg
> automatically.

---

## Project layout

```
Comfy-FlashVSR-Trunk/
├── __init__.py              # Node registration
├── nodes.py                 # 4 node definitions
├── trunk_core.py            # Core: chunk planning / FlashVSR call / ffmpeg IO & merge
├── requirements.txt         # pip dependency (imageio-ffmpeg)
├── install.py               # ComfyUI Manager hook: deps + auto-clone peer
├── node.json                # ComfyUI Manager metadata
├── README.md                # Chinese docs
├── README_EN.md             # This file
├── LICENSE                  # MIT
├── tests/test_trunk.py      # Test suite (42 tests, runs without pytest)
├── scripts/make_demo.py     # Regenerates the example workflow reproducibly
├── .github/workflows/ci.yml # CI on Python 3.11 / 3.12
├── example_workflows/       # Drop-in demo (VHS -> Trunk_Frames -> VHS)
└── legacy/                  # ⚠️ Historical archive only, superseded by this plugin
```

---

## Uninstall

Delete `custom_nodes/Comfy-FlashVSR-Trunk` and restart ComfyUI. No leftovers.

---

## Known limitations

- File Pipeline mode needs write access to the source video's directory (or your `output_dir`).
- **FlashVSR Trunk ⚡ Frames** returns the whole upscaled tensor — for very long
  4x clips use the File Pipeline instead.
- The first run still downloads model weights via `ComfyUI-FlashVSR`, same as using
  FlashVSR directly.

---

*Trunk = one main line for "the FlashVSR pipeline that actually handles long videos",
so you only think about input and output.*
