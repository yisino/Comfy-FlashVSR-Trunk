#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comfy-FlashVSR-Trunk · ComfyUI 节点定义
========================================

提供 4 个节点，覆盖「FlashVSR 长视频 4x 分块超分」的完整流水线：

1. FlashVSR_Trunk            —— 文件流水线（基础 preset）。给定源视频路径即自动
                                分块上采样 + 合并，返回最终 mp4 路径 + 预览帧。
                                对长视频完全 RAM-safe（逐块落盘）。
2. FlashVSR_Trunk_Advanced   —— 同上，但暴露高级模型参数（model_version / tiling /
                                tile_size / quality_boost ...）。
3. FlashVSR_Trunk_Frames     —— 即插即用的 IMAGE->IMAGE 节点，可直接替换原
                                AILab_FlashVSR，内部做时间分块，规避 4x 整段 OOM。
                                适合能放进显存/内存的较短片段。
4. FlashVSR_Trunk_Merge      —— 独立的分块视频合并节点（重叠去重 + 复用源音频），
                                对应历史 merge_overlap.py，无需 GPU。
"""

import os
import glob

import torch

from . import trunk_core as tc


# ===========================================================================
# ComfyUI INPUT_TYPES 公共字段（4 个节点共用，集中维护避免漂移）
# ===========================================================================
def _common_chunk_io_params():
    """时间分块相关参数：chunk_size / overlap / unload_model / seed。
    4 个节点的 required 字典合并此 dict 即可获得一致的分块控制。
    """
    return {
        "chunk_size": ("INT", {"default": 128, "min": 24, "max": 512, "step": 8,
                                "tooltip": "每块帧数。4x 时建议 <=160 以避开 CPU 画布 OOM；越大越快越占内存"}),
        "overlap": ("INT", {"default": 16, "min": 0, "max": 64, "step": 4,
                             "tooltip": "相邻块重叠帧数（合并时裁掉），越大接缝越平滑但越慢"}),
        "unload_model": ("BOOLEAN", {"default": True,
                                       "tooltip": "逐块后释放模型占用（流水线已逐块处理，建议 True）"}),
        "seed": ("INT", {"default": 1, "min": 1, "max": 0xFFFFFFFFFFFFFFFF}),
    }


def _common_output_params(default_name, fps_default_tooltip="0 = 沿用源帧率"):
    """输出路径 / fps / crf / 输出目录。"""
    return {
        "output_name": ("STRING", {"default": default_name}),
        "fps": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 240.0, "step": 0.1,
                            "tooltip": fps_default_tooltip}),
        "crf": ("INT", {"default": 19, "min": 0, "max": 51, "tooltip": "输出质量，越小越好越大"}),
        "output_dir": ("STRING", {"default": "", "placeholder": "留空=源视频同目录"}),
    }


def _run_file_pipeline_node(src_video, params, advanced, *,
                             chunk_size, overlap, output_name,
                             fps, crf, output_dir, default_output_name):
    """File Pipeline 节点 (FlashVSR_Trunk / Advanced) 共享的执行体：
    校验输入 -> 解析输出路径 -> 跑 tc.run_file_pipeline -> 生成预览帧。
    返回 (final_path, preview_tensor)。
    """
    if not src_video or not os.path.exists(src_video):
        raise FileNotFoundError(f"源视频不存在: {src_video}")
    out_dir = output_dir.strip() or os.path.dirname(os.path.abspath(src_video))
    os.makedirs(out_dir, exist_ok=True)
    name = (output_name or "").strip() or default_output_name
    out_path = os.path.join(out_dir, f"{name}.mp4")
    final = tc.run_file_pipeline(
        src_video, params, advanced=advanced, out_path=out_path,
        chunk_size=chunk_size, overlap=overlap, fps=(fps or None), crf=crf,
    )
    preview = _preview_from_video(final, fps or None)
    return final, preview


# ===========================================================================
# 1) 文件流水线（基础 preset）
# ===========================================================================
class FlashVSR_Trunk:
    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "src_video": ("STRING", {"default": "", "placeholder": "源视频绝对路径 (mp4)", "multiline": False}),
            "preset": (["Fast (2x Speed)", "Balanced (2x Quality)", "Long Video (Low VRAM)", "High Quality (Best)"],
                       {"default": "Balanced (2x Quality)"}),
            "scale": ("INT", {"default": 4, "min": 2, "max": 4, "step": 2, "tooltip": "放大倍数，4x 质量最佳"}),
        }
        required.update(_common_chunk_io_params())
        required.update(_common_output_params("FlashVSR_Trunk"))
        return {"required": required}

    RETURN_TYPES = ("STRING", "IMAGE")
    RETURN_NAMES = ("VIDEO_PATH", "PREVIEW")
    FUNCTION = "run"
    CATEGORY = "🧪AILab/⚡FlashVSR/Trunk"

    def run(self, src_video, preset, scale, chunk_size, overlap, unload_model,
            seed, output_name, fps, crf, output_dir):
        params = tc.basic_params(preset, scale, unload_model, seed)
        final, preview = _run_file_pipeline_node(
            src_video, params, advanced=False,
            chunk_size=chunk_size, overlap=overlap, output_name=output_name,
            fps=fps, crf=crf, output_dir=output_dir,
            default_output_name="FlashVSR_Trunk",
        )
        return (final, preview)


# ===========================================================================
# 2) 文件流水线（高级参数）
# ===========================================================================
class FlashVSR_Trunk_Advanced:
    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "src_video": ("STRING", {"default": "", "placeholder": "源视频绝对路径 (mp4)"}),
            "model_version": (["Tiny (Fast)", "Tiny Long (Low VRAM)", "Full (Best Quality)"],
                              {"default": "Full (Best Quality)"}),
            "scale": ("INT", {"default": 4, "min": 2, "max": 4, "step": 2}),
            "enable_tiling": ("BOOLEAN", {"default": True, "tooltip": "空间分块，省显存"}),
            "tile_size": ("INT", {"default": 384, "min": 128, "max": 1024, "step": 32}),
            "tile_overlap": ("INT", {"default": 24, "min": 8, "max": 256, "step": 8}),
            "speed_optimization": ("FLOAT", {"default": 2.0, "min": 1.5, "max": 2.0, "step": 0.1}),
            "quality_boost": ("FLOAT", {"default": 2.0, "min": 1.0, "max": 3.0, "step": 0.1}),
            "stability_level": ([9, 11], {"default": 11}),
            "color_fix": ("BOOLEAN", {"default": True}),
            "vae_tiling": ("BOOLEAN", {"default": True}),
            "unload_model": ("BOOLEAN", {"default": True}),
            "sageattention": (["enable", "disable"], {"default": "enable"}),
            "device": (["auto", "cuda", "cpu"], {"default": "auto"}),
            "precision": (["bf16", "fp16"], {"default": "bf16"}),
            "seed": ("INT", {"default": 1, "min": 1, "max": 0xFFFFFFFFFFFFFFFF}),
        }
        required.update(_common_output_params("FlashVSR_Trunk"))
        # Advanced 节点的分块参数 tooltip 与基础节点一致（chunk_size / overlap 来自 _common）
        required.update(_common_chunk_io_params())
        return {"required": required}

    RETURN_TYPES = ("STRING", "IMAGE")
    RETURN_NAMES = ("VIDEO_PATH", "PREVIEW")
    FUNCTION = "run"
    CATEGORY = "🧪AILab/⚡FlashVSR/Trunk"

    def run(self, src_video, model_version, scale, enable_tiling, tile_size, tile_overlap,
            speed_optimization, quality_boost, stability_level, color_fix, vae_tiling,
            unload_model, sageattention, device, precision, seed,
            chunk_size, overlap, output_name, fps, crf, output_dir):
        params = tc.advanced_params(
            model_version, scale, enable_tiling, tile_size, tile_overlap,
            speed_optimization, quality_boost, stability_level, color_fix,
            vae_tiling, unload_model, device, precision, seed)
        final, preview = _run_file_pipeline_node(
            src_video, params, advanced=True,
            chunk_size=chunk_size, overlap=overlap, output_name=output_name,
            fps=fps, crf=crf, output_dir=output_dir,
            default_output_name="FlashVSR_Trunk",
        )
        return (final, preview)


# ===========================================================================
# 3) 即插即用 IMAGE -> IMAGE（短片段 / VHS 图）
# ===========================================================================
class FlashVSR_Trunk_Frames:
    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "frames": ("IMAGE", {"tooltip": "低分辨率视频帧 (IMAGE 张量)"}),
            "preset": (["Fast (2x Speed)", "Balanced (2x Quality)", "Long Video (Low VRAM)", "High Quality (Best)"],
                       {"default": "Balanced (2x Quality)"}),
            "scale": ("INT", {"default": 4, "min": 2, "max": 4, "step": 2}),
        }
        # Drop-in 节点只用 chunk_size / overlap / unload / seed（不输出视频文件）
        ci = _common_chunk_io_params()
        required.update({k: ci[k] for k in ("chunk_size", "overlap", "unload_model", "seed")})
        return {
            "required": required,
            "optional": {"audio": ("AUDIO",)},
        }

    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("FRAMES", "AUDIO")
    FUNCTION = "upscale"
    CATEGORY = "🧪AILab/⚡FlashVSR/Trunk"

    def upscale(self, frames, preset, scale, chunk_size, overlap, unload_model, seed, audio=None):
        params = tc.basic_params(preset, scale, unload_model, seed)
        out = tc.upscale_frames_chunked(
            frames, params, advanced=False, chunk_size=chunk_size, overlap=overlap)
        return (out, audio)


# ===========================================================================
# 4) 分块视频合并（独立节点，无需 GPU）
# ===========================================================================
class FlashVSR_Trunk_Merge:
    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "chunk_dir": ("STRING", {"default": "", "placeholder": "分块视频所在目录"}),
            "name_pattern": ("STRING", {"default": "FlashVSR_ovchunk*.mp4", "multiline": False}),
            "overlap": ("INT", {"default": 16, "min": 0, "max": 128, "step": 4,
                                "tooltip": "生成分块时的重叠帧数（合并时裁掉）"}),
        }
        # Merge 节点使用 chunk_dir/output_dir/fps/crf/output_name，不再需要 chunk_size
        out_params = _common_output_params("FlashVSR_final", fps_default_tooltip="0 = 沿用分块帧率")
        required.update({k: out_params[k] for k in ("output_name", "fps", "crf", "output_dir")})
        return {
            "required": required,
            "optional": {"source_video": ("STRING", {"default": "", "placeholder": "源视频路径（用于复用音频），可留空"})},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("VIDEO_PATH",)
    FUNCTION = "merge"
    CATEGORY = "🧪AILab/⚡FlashVSR/Trunk"

    def merge(self, chunk_dir, name_pattern, overlap, output_name, crf, fps, output_dir, source_video=""):
        if not chunk_dir or not os.path.isdir(chunk_dir):
            raise FileNotFoundError(f"分块目录不存在: {chunk_dir}")
        files = sorted(glob.glob(os.path.join(chunk_dir, name_pattern)))
        if not files:
            raise FileNotFoundError(f"未找到匹配的分块: {os.path.join(chunk_dir, name_pattern)}")
        out_dir = output_dir.strip() or chunk_dir
        os.makedirs(out_dir, exist_ok=True)
        name = (output_name or "").strip() or "FlashVSR_final"
        out_path = os.path.join(out_dir, f"{name}.mp4")
        src = source_video.strip() if source_video and os.path.exists(source_video) else None
        final = tc.merge_chunk_videos(
            files, out_path, overlap, source_audio_path=src,
            fps=(fps or None), crf=crf)
        return (final,)


# ---------------------------------------------------------------------------
# 预览辅助：从最终视频读取前几帧作为 IMAGE 预览
# ---------------------------------------------------------------------------
def _preview_from_video(path, fps):
    try:
        ff = tc.get_ffmpeg()
        n = 8
        arr = tc.read_frames_ffmpeg(path, 0, n, ff)  # uint8 RGB [N,H,W,3]
        if arr is None or arr.shape[0] == 0:
            return torch.zeros((1, 8, 8, 3), dtype=torch.float32)
        t = torch.from_numpy(arr.astype("float32") / 255.0)
        return t
    except Exception:
        return torch.zeros((1, 8, 8, 3), dtype=torch.float32)
