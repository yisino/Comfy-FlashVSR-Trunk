#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comfy-FlashVSR-Trunk · 核心逻辑
================================

把所有 FlashVSR 修改（4x 重叠分块 + CPU OOM 规避 + 自动合并）封装为一个
可被 ComfyUI 节点直接调用的 Python 模块。

要点：
- FlashVSR 的原生节点在整段长视频 4x 时会一次性在 CPU 上分配
  `torch.zeros((nf, oh, ow, C))` 画布（如 759 帧 4x ≈ 60.9GB），直接 OOM。
- 本模块按「时间分块 + 相邻重叠 + 合并时裁掉重叠区」的方式，逐块调用 FlashVSR，
  每块只分配「单块画布」（≈10GB），从而把整段长视频也跑得动。
- 文件流水线模式（FlashVSR_Trunk 节点）进一步把每块结果立刻落盘再合并，
  连「返回整段张量」这一步的 60GB 内存压力也规避掉，对长视频完全 RAM-safe。

依赖：ComfyUI 运行环境自带 torch / numpy；ffmpeg 复用 imageio-ffmpeg 内置二进制
（与 VHS 一致），找不到时退回系统 ffmpeg。
"""

import os
import sys
import glob
import shutil
import subprocess
import importlib

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
MIN_FRAMES = 21  # FlashVSR 要求的最少帧数

# 基础节点 preset -> 内部参数 (mode, sr, kvr, lr, td, tv, ts, to)
# 与 ComfyUI-FlashVSR/AILab_FlashVSR.py 中的 presets 映射保持一致
_BASIC_PRESETS = {
    "Fast (2x Speed)":          ("tiny",      1.5, 1.0, 9,  True, True, 256, 32),
    "Balanced (2x Quality)":    ("tiny",      2.0, 2.0, 11, True, True, 256, 32),
    "Long Video (Low VRAM)":    ("tiny-long", 2.0, 2.0, 11, True, True, 256, 32),
    "High Quality (Best)":      ("full",      2.0, 3.0, 11, True, True, 256, 32),
}
# 高级节点 model_version -> mode
_ADV_MODE_MAP = {
    "Tiny (Fast)":          "tiny",
    "Tiny Long (Low VRAM)": "tiny-long",
    "Full (Best Quality)":  "full",
}
# mode -> 反推 preset（仅用于 fallback 路径，尽力匹配）
_MODE_TO_BASIC = {
    "tiny":      "Balanced (2x Quality)",
    "tiny-long": "Long Video (Low VRAM)",
    "full":      "High Quality (Best)",
}


# ---------------------------------------------------------------------------
# FlashVSR 模块解析
# ---------------------------------------------------------------------------
class FlashVSRNotInstalled(RuntimeError):
    pass


def import_flashvsr():
    """导入已安装的 ComfyUI-FlashVSR 的 AILab_FlashVSR 模块。

    返回模块对象；若未安装则抛出 FlashVSRNotInstalled（含安装指引）。
    """
    # 1) 直接按模块名导入（ComfyUI 会把 custom_nodes/ComfyUI-FlashVSR 加入 sys.path）
    try:
        return importlib.import_module("AILab_FlashVSR")
    except Exception:
        pass
    # 2) 作为包的子模块导入
    try:
        return importlib.import_module("ComfyUI_FlashVSR.AILab_FlashVSR")
    except Exception:
        pass
    # 3) 文件系统扫描 custom_nodes/ComfyUI-FlashVSR/AILab_FlashVSR.py
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        for root in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
            hits = glob.glob(os.path.join(root, "**", "ComfyUI-FlashVSR", "AILab_FlashVSR.py"),
                             recursive=True)
            if hits:
                d = os.path.dirname(hits[0])
                if d not in sys.path:
                    sys.path.insert(0, d)
                return importlib.import_module("AILab_FlashVSR")
    except Exception:
        pass
    raise FlashVSRNotInstalled(
        "未找到已安装的 ComfyUI-FlashVSR 节点。请先通过 ComfyUI Manager 或 "
        "git clone https://github.com/1038lab/ComfyUI-FlashVSR 安装到 custom_nodes，"
        "重启 ComfyUI 后再使用本插件（本插件依赖其模型与推理代码）。"
    )


# ---------------------------------------------------------------------------
# 分块规划
# ---------------------------------------------------------------------------
def plan_chunks(total, chunk_size, overlap, min_frames=MIN_FRAMES):
    """把 [0, total) 帧规划为带重叠的时间分块列表 [(s, e), ...]。

    - 每块长度不超过 chunk_size；
    - 相邻块首尾各重叠 `overlap` 帧（边界落在重叠内，保证时域连续）；
    - 末块若 < min_frames，则并入前一块（避免 FlashVSR 因帧数不足报错）。
    返回的每个 (s, e) 满足 0 <= s < e <= total。
    """
    chunk_size = max(1, int(chunk_size))
    overlap = max(0, int(overlap))
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)
    step = max(1, chunk_size - overlap)

    chunks = []
    s = 0
    while s < total:
        e = min(total, s + chunk_size)
        chunks.append([s, e])
        if e >= total:        # 已到视频末尾，跳出（否则 s=total-overlap 仍 <total 会死循环）
            break
        s = e - overlap
    # 末块过短则并入前一块
    while len(chunks) >= 2 and (chunks[-1][1] - chunks[-1][0]) < min_frames:
        chunks[-2][1] = chunks[-1][1]
        chunks.pop()
    return [(s, e) for s, e in chunks]


def _trim_overlap(up, s, e, total, overlap):
    """按分块位置裁掉重叠区（Convention B：除首块外，各块丢弃头部重叠帧，
    尾部全部保留）。保证相邻块连续、无丢帧、无重复。

    数学上：块 i(>0) 丢弃头部 overlap 帧 -> 保留 [s_i+overlap, e_i)；
    块 i-1 保留到 e_{i-1}=s_i，二者在 s_i 处连续，重叠区被块 i-1 的尾部保留。
    """
    L = up.shape[0]
    ts = overlap if s > 0 else 0
    if ts == 0:
        return up
    return up[ts:]


# ---------------------------------------------------------------------------
# 参数构造
# ---------------------------------------------------------------------------
def basic_params(preset, scale, unload_model, seed):
    mode, sr, kvr, lr, td, tv, ts, to = _BASIC_PRESETS[preset]
    return dict(mode=mode, scale=scale, tiling=td, ts=ts, to=to, sr=sr, kvr=kvr,
                lr=lr, cf=True, ud=unload_model, tv=tv, seed=seed,
                device="auto", dtype="bf16")


def advanced_params(model_version, scale, enable_tiling, tile_size, tile_overlap,
                    speed_optimization, quality_boost, stability_level, color_fix,
                    vae_tiling, unload_model, device, precision, seed):
    return dict(mode=_ADV_MODE_MAP[model_version], scale=scale, tiling=enable_tiling,
                ts=tile_size, to=tile_overlap, sr=speed_optimization,
                kvr=quality_boost, lr=stability_level, cf=color_fix,
                ud=unload_model, tv=vae_tiling, seed=seed,
                device=device, dtype=precision)


def _upscale_kwargs(params, advanced):
    """构造调用 AILab_FlashVSR / AILab_FlashVSR_Advanced.upscale() 的关键字参数
    （fallback 路径用）。"""
    if advanced:
        return dict(
            model_version={v: k for k, v in _ADV_MODE_MAP.items()}[params["mode"]],
            scale=params["scale"], enable_tiling=params["tiling"],
            tile_size=params["ts"], tile_overlap=params["to"],
            speed_optimization=params["sr"], quality_boost=params["kvr"],
            stability_level=params["lr"], color_fix=params["cf"],
            vae_tiling=params["tv"], unload_model=params["ud"],
            device=params["device"],
            precision="fp16" if params["dtype"] == "fp16" else "bf16",
            seed=params["seed"],
        )
    return dict(preset=_MODE_TO_BASIC.get(params["mode"], "Balanced (2x Quality)"),
                scale=params["scale"], unload_model=params["ud"], seed=params["seed"])


# ---------------------------------------------------------------------------
# FlashVSR 管道封装（模型只加载一次，逐块复用）
# ---------------------------------------------------------------------------
_EFFICIENT_ATTRS = ("_setup_device_and_dtype", "init_pipe", "_pad_video_sequence",
                    "_restore_video_sequence", "_tile", "_full")


class FlashVSRPipe:
    """持有一个已加载的 FlashVSR 管道，供多块复用。

    优先走「高效路径」：直接调用模块内部的 init_pipe / _tile / _full，
    模型只加载一次，逐块处理（每块分配单块画布，不 OOM）。
    若内部函数不可用（版本变更），自动降级为「每块调用一次 upscale()」。
    """

    def __init__(self, params, advanced):
        self.mod = import_flashvsr()
        self.params = params
        self.advanced = advanced
        self.efficient = all(hasattr(self.mod, a) for a in _EFFICIENT_ATTRS)
        self.dev, self.dt = self.mod._setup_device_and_dtype(params["device"], params["dtype"])
        self.pipe = self.mod.init_pipe(params["mode"], self.dev, self.dt)

    def upscale_chunk(self, cf):
        """cf: [L,H,W,3] float32 张量（单块帧）。返回同帧数的上采样张量。"""
        p = self.params
        if self.efficient:
            pf, added = self.mod._pad_video_sequence(cf)
            if p["tiling"]:
                res = self.mod._tile(pf, self.pipe, p["scale"], p["ts"], p["to"],
                                     p["sr"], p["kvr"], p["lr"], p["cf"],
                                     p["ud"], p["tv"], p["seed"], self.dev, self.dt)
            else:
                res = self.mod._full(pf, self.pipe, p["scale"], p["sr"], p["kvr"],
                                     p["lr"], p["cf"], p["ud"], p["tv"], p["seed"],
                                     self.dev, self.dt)
            return self.mod._restore_video_sequence(res, added, cf.shape[0])
        # fallback：每块调用一次节点 upscale()
        cls = self.mod.AILab_FlashVSR_Advanced if self.advanced else self.mod.AILab_FlashVSR
        kws = _upscale_kwargs(p, self.advanced)
        kws["frames"] = cf
        kws["audio"] = None
        return cls().upscale(**kws)[0]

    def close(self):
        try:
            del self.pipe
        except Exception:
            pass
        try:
            self.mod.clean_vram()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 上采样入口（张量版，用于 drop-in 节点）
# ---------------------------------------------------------------------------
def upscale_frames_chunked(frames, params, advanced, chunk_size, overlap):
    """对整段 frames 张量做重叠分块上采样，返回拼接后的张量。

    frames: torch.Tensor [B,H,W,3] float32 in [0,1]
    返回:    torch.Tensor [B',H*scale,W*scale,3]
    B' == 原始帧数（重叠区已被裁掉，无重复、无丢帧）。
    """
    import torch
    total = frames.shape[0]
    if total < MIN_FRAMES:
        raise ValueError(f"FlashVSR 至少需要 {MIN_FRAMES} 帧，当前仅 {total} 帧。")

    chunks = plan_chunks(total, chunk_size, overlap)
    pipe = FlashVSRPipe(params, advanced)
    try:
        outs = []
        for (s, e) in chunks:
            cf = frames[s:e]
            up = pipe.upscale_chunk(cf)
            up = _trim_overlap(up, s, e, total, overlap)
            outs.append(up)
    finally:
        pipe.close()
    return torch.cat(outs, dim=0)


# ---------------------------------------------------------------------------
# ffmpeg 定位 + 视频 IO（文件流水线模式用）
# ---------------------------------------------------------------------------
def get_ffmpeg():
    """优先复用 imageio-ffmpeg 内置二进制（与 VHS 一致），否则退回系统 ffmpeg。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    raise RuntimeError("找不到 ffmpeg：请安装 imageio-ffmpeg（pip install imageio-ffmpeg）或系统 ffmpeg。")


def probe_frames(path, ff=None):
    ff = ff or get_ffmpeg()
    r = subprocess.run([ff, "-hide_banner", "-i", path, "-map", "0:v:0",
                        "-c", "copy", "-f", "null", "-"],
                       capture_output=True, text=True)
    last = [l for l in r.stderr.splitlines() if l.strip().startswith("frame=")]
    if not last:
        return None
    import re
    m = re.search(r"frame\s*=\s*(\d+)", last[-1])
    return int(m.group(1)) if m else None


def probe_size(path, ff=None):
    """返回 (width, height, fps)。优先 ffprobe，缺失则用 ffmpeg 解析。"""
    ff = ff or get_ffmpeg()
    try:
        import json
        fp = shutil.which("ffprobe")
        if fp:
            out = subprocess.run([fp, "-v", "error", "-select_streams", "v:0",
                                  "-show_entries", "stream=width,height,avg_frame_rate",
                                  "-of", "json", path],
                                 capture_output=True, text=True).stdout
            d = json.loads(out).get("streams", [{}])[0]
            w, h = int(d["width"]), int(d["height"])
            fr = d.get("avg_frame_rate", "0/1")
            num, _, den = fr.partition("/")
            fps = float(num) / float(den) if den not in ("", "0") and float(den) else 0.0
            return w, h, fps
    except Exception:
        pass
    # ffmpeg 回退：解析 stderr 中的 Stream #0:0: Video: ... 与 fps
    r = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True)
    w = h = 0
    fps = 0.0
    for line in r.stderr.splitlines():
        if "Video:" in line:
            import re
            m = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
            mf = re.search(r"(\d+(?:\.\d+)?)\s*fps", line)
            if mf:
                fps = float(mf.group(1))
    return w, h, fps


def read_frames_ffmpeg(path, start, end, ff=None):
    """用 ffmpeg 提取 [start, end) 帧区间，返回 uint8 RGB numpy 数组 [N,H,W,3]。"""
    import numpy as np
    ff = ff or get_ffmpeg()
    w, h, _ = probe_size(path, ff)
    n = end - start
    # 注意：以 argv 列表传参时不要加 shell 引号，select 内部的逗号用反斜杠转义
    cmd = [ff, "-hide_banner", "-i", path,
           "-vf", f"select=gte(n\\,{start})*lte(n\\,{end-1}),setpts=N/FRAME_RATE/TB",
           "-vsync", "0", "-frames:v", str(n), "-pix_fmt", "rgb24",
           "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True).stdout
    got = len(out) // (w * h * 3)
    if got != n:
        # 某些编码器 select 计数略有偏差，按实际字节数截取
        n = got
    arr = np.frombuffer(out[: n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)
    return arr


def write_frames_ffmpeg(frames, w, h, fps, out_path, crf=19, ff=None):
    """把 uint8 RGB 数组 [N,H,W,3] 写成 h264 mp4（经 ffmpeg pipe）。"""
    import numpy as np
    ff = ff or get_ffmpeg()
    proc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-pix_fmt", "yuv420p", "-c:v", "libx264",
         "-crf", str(crf), "-movflags", "+faststart", out_path],
        stdin=subprocess.PIPE)
    proc.stdin.write(np.ascontiguousarray(frames).tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"写 chunk 视频失败 (ffmpeg rc={proc.returncode}): {out_path}")


# ---------------------------------------------------------------------------
# 分块视频合并（重叠去重 + 复用源音频）
# ---------------------------------------------------------------------------
def merge_chunk_videos(chunk_paths, out_path, overlap, source_audio_path=None,
                       fps=None, crf=19, pix_fmt="yuv420p", ff=None):
    """拼接分块视频：每段裁掉重叠区（头/尾各 overlap 帧），最后复用源音频合成最终 mp4。

    与历史 merge_overlap.py 逻辑一致，但做成可复用函数。
    返回最终文件路径。
    """
    ff = ff or get_ffmpeg()
    tmp = out_path + ".trunk_tmp"
    os.makedirs(tmp, exist_ok=True)
    try:
        segs = []
        n = len(chunk_paths)
        for i, p in enumerate(chunk_paths):
            F = probe_frames(p, ff)
            if F is None:
                raise RuntimeError(f"无法探测分块帧数: {p}")
            # Convention B：首块保留全部；其余块丢弃头部 overlap 帧（尾部保留），
            # 保证相邻块在边界处连续、无丢帧、无重复。
            # 用 trim=start_frame 按「帧索引」精确裁切（比 select 过滤器更可靠，
            # 不会在块边界多丢帧）；-vsync 0 保留被裁后的精确帧数。
            if i > 0 and overlap and F > overlap:
                vf = f"trim=start_frame={overlap},setpts=PTS-STARTPTS"
            else:
                vf = "setpts=PTS-STARTPTS"
            outp = os.path.join(tmp, f"seg_{i+1:02d}.mp4")
            cmd = [ff, "-hide_banner", "-i", p, "-vf", vf, "-vsync", "0",
                   "-pix_fmt", pix_fmt, "-crf", str(crf), "-c:a", "copy", outp]
            subprocess.run(cmd, check=True)
            segs.append(outp)

        listf = os.path.join(tmp, "list.txt")
        with open(listf, "w", encoding="utf-8") as f:
            for s in segs:
                f.write(f"file '{s}'\n")

        cmd = [ff, "-hide_banner", "-f", "concat", "-safe", "0", "-i", listf]
        if source_audio_path and os.path.exists(source_audio_path):
            # 分块均为统一参数 libx264，直接 copy 拼接可保证「帧数精确」（不重新编码，
            # 不会因 PTS 不连续丢帧）；音频单独复用源视频音轨。
            cmd += ["-i", source_audio_path, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", out_path]
        else:
            cmd += ["-c:v", "copy", out_path]
        subprocess.run(cmd, check=True)
    finally:
        # 清理临时段文件
        try:
            for f in glob.glob(os.path.join(tmp, "*")):
                os.remove(f)
            os.rmdir(tmp)
        except Exception:
            pass
    return out_path


# ---------------------------------------------------------------------------
# 文件流水线：读源视频 -> 逐块上采样落盘 -> 合并
# ---------------------------------------------------------------------------
def run_file_pipeline(src_video, params, advanced, out_path, chunk_size=128,
                      overlap=16, fps=None, crf=19, ff=None, log=print):
    """完整的 RAM-safe 文件流水线（FlashVSR_Trunk 节点核心）。

    1. 探测总帧数；
    2. 规划重叠分块；
    3. 加载一次 FlashVSR 管道，逐块：ffmpeg 读帧 -> 上采样 -> 写 chunk mp4 -> 释放；
    4. 合并所有 chunk（重叠去重 + 复用源音频）-> 最终 mp4。

    返回最终视频路径。每块最多只在内存中保留单块数据，长视频也不 OOM。
    """
    import numpy as np
    import torch
    ff = ff or get_ffmpeg()
    total = probe_frames(src_video, ff)
    if total is None or total < MIN_FRAMES:
        raise ValueError(f"源视频帧数无效或不足 {MIN_FRAMES} 帧: {src_video}")
    w, h, src_fps = probe_size(src_video, ff)
    if not fps:
        fps = src_fps or 24
    log(f"[Trunk] 源视频 {total} 帧 / {w}x{h} / {fps:.2f}fps，分块(chunk={chunk_size}, overlap={overlap})")

    chunks = plan_chunks(total, chunk_size, overlap)
    tmpdir = out_path + ".chunks"
    os.makedirs(tmpdir, exist_ok=True)
    chunk_paths = []
    pipe = FlashVSRPipe(params, advanced)
    try:
        for idx, (s, e) in enumerate(chunks):
            arr = read_frames_ffmpeg(src_video, s, e, ff)  # uint8 RGB [L,H,W,3]
            # 转 ComfyUI 格式张量 [L,H,W,3] float32 [0,1]
            cf = torch.from_numpy(arr.astype("float32") / 255.0)
            up = pipe.upscale_chunk(cf)  # [L, H*scale, W*scale, 3]
            up8 = (up.clamp(0, 1).cpu().numpy() * 255.0).astype("uint8")
            oh, ow = up8.shape[1], up8.shape[2]
            cp = os.path.join(tmpdir, f"chunk_{idx+1:03d}.mp4")
            write_frames_ffmpeg(up8, ow, oh, fps, cp, crf=crf, ff=ff)
            chunk_paths.append(cp)
            log(f"[Trunk] 块 {idx+1}/{len(chunks)} 完成 -> {os.path.basename(cp)}")
    finally:
        pipe.close()

    log("[Trunk] 合并分块（重叠去重 + 复用源音频）...")
    merge_chunk_videos(chunk_paths, out_path, overlap,
                       source_audio_path=src_video, fps=fps, crf=crf, ff=ff)
    # 清理临时 chunk
    try:
        for p in chunk_paths:
            os.remove(p)
        os.rmdir(tmpdir)
    except Exception:
        pass
    log(f"[Trunk] 完成: {out_path}")
    return out_path
