#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Comfy-FlashVSR-Trunk contributors
"""
Comfy-FlashVSR-Trunk 测试套件
=============================

运行方式（二选一）：
    python tests/test_trunk.py        # 独立运行，无需 pytest
    python -m pytest tests/ -v        # 若已安装 pytest

覆盖范围：
    A. 节点注册与 ComfyUI 契约（4 节点 / 签名与 INPUT_TYPES 对齐）
    B. 分块规划 plan_chunks（覆盖 / 重叠 / 最小长度 / 退化 / 无死循环 / 边界）
    C. FlashVSR 模块解析（路径发现与重导入解耦）
    D. ffmpeg 定位与视频 IO（读 / 写 / 探测）
    E. 端到端流水线（合成视频 -> 重叠分块 -> 写分块 -> 合并，帧数精确）
    F. 张量重建 _trim_overlap（Convention B）
    G. 参数构造与 fallback 映射
    H. 错误处理与异常边界
    I. 代码质量：type hints 完整性

说明：真实模型推理需 ComfyUI + GPU 运行时；本套件只测「不依赖 GPU」的部分，
      真实模型加载由 ComfyUI 在节点执行时完成。
"""

import glob
import inspect
import os
import subprocess
import sys
import tempfile
import typing

import numpy as np

# ---------------------------------------------------------------------------
# 加载被测插件包（目录名含连字符，需 spec_from_file_location）
# ---------------------------------------------------------------------------
class _Skip(Exception):
    """测试环境不满足前置条件时抛出；运行器记为 SKIP，不计入失败。

    典型场景：CI 只克隆了 peer 而没有完整 ComfyUI 检出，
    ``import comfy.utils`` 不可用 -> test_c4 跳过而非误报失败。
    """


CUSTOM_NODES = os.environ.get(
    "COMFY_CUSTOM_NODES",
    r"D:/Comfy-Desktop/ComfyUI-Installs/NVIDIA/ComfyUI/custom_nodes",
)
# ComfyUI 根目录（提供 folder_paths / comfy），用于真实导入 peer 的集成测试
COMFY_ROOT = os.environ.get("COMFY_ROOT") or os.path.dirname(CUSTOM_NODES)
PKG_DIR = os.path.join(CUSTOM_NODES, "Comfy-FlashVSR-Trunk")

sys.path.insert(0, CUSTOM_NODES)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ComfyFlashVSRTrunk",
    os.path.join(PKG_DIR, "__init__.py"),
    submodule_search_locations=[PKG_DIR],
)
pkg = importlib.util.module_from_spec(_spec)
sys.modules["ComfyFlashVSRTrunk"] = pkg
_spec.loader.exec_module(pkg)

tc = pkg.trunk_core
nodes = pkg.nodes

MIN_FRAMES = tc.MIN_FRAMES


# ---------------------------------------------------------------------------
# 合成测试视频（ffmpeg lavfi，无需外部素材）
# ---------------------------------------------------------------------------
def make_test_video(path, n_frames, size=64, rate=1, ff=None):
    ff = ff or tc.get_ffmpeg()
    subprocess.run(
        [ff, "-y", "-hide_banner", "-f", "lavfi",
         "-i", f"testsrc=size={size}x{size}:rate={rate}:duration={n_frames}",
         "-pix_fmt", "rgb24", "-r", str(rate), path],
        check=True, capture_output=True,
    )
    return path


# ===========================================================================
# A. 节点注册与 ComfyUI 契约
# ===========================================================================
def test_a1_four_nodes_registered():
    keys = set(pkg.NODE_CLASS_MAPPINGS)
    assert keys == {"FlashVSR_Trunk", "FlashVSR_Trunk_Advanced",
                    "FlashVSR_Trunk_Frames", "FlashVSR_Trunk_Merge"}, keys


def test_a2_display_names_match():
    assert set(pkg.NODE_DISPLAY_NAME_MAPPINGS) == set(pkg.NODE_CLASS_MAPPINGS)


def test_a3_comfyui_contract():
    """每个节点必须有 INPUT_TYPES / FUNCTION / RETURN_TYPES / RETURN_NAMES / CATEGORY，
    且 FUNCTION 指向真实存在的方法。"""
    for key, cls in pkg.NODE_CLASS_MAPPINGS.items():
        it = cls.INPUT_TYPES()
        assert isinstance(it, dict), f"{key}: INPUT_TYPES 非 dict"
        assert isinstance(it.get("required"), dict), f"{key}: 缺 required"
        fn_name = getattr(cls, "FUNCTION", None)
        assert fn_name, f"{key}: 缺 FUNCTION"
        assert callable(getattr(cls, fn_name, None)), f"{key}: FUNCTION={fn_name} 不存在"
        assert isinstance(cls.RETURN_TYPES, tuple), f"{key}: RETURN_TYPES 非 tuple"
        if hasattr(cls, "RETURN_NAMES"):
            assert len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES), \
                f"{key}: RETURN_TYPES/NAMES 长度不符"
        assert cls.CATEGORY == "🧪AILab/⚡FlashVSR/Trunk", f"{key}: CATEGORY 不符"


def test_a4_signature_matches_input_types():
    """FUNCTION 方法的形参数（除 self）必须与 INPUT_TYPES 的 required+optional 键一致，
    否则 ComfyUI 调用时会 TypeError。"""
    for key, cls in pkg.NODE_CLASS_MAPPINGS.items():
        it = cls.INPUT_TYPES()
        widget_keys = set(it.get("required", {})) | set(it.get("optional", {}))
        sig = inspect.signature(getattr(cls, cls.FUNCTION))
        params = {p for p in sig.parameters if p != "self"}
        assert params == widget_keys, (
            f"{key}: 签名与 INPUT_TYPES 不符\n"
            f"  仅签名有: {sorted(params - widget_keys)}\n"
            f"  仅 INPUT_TYPES 有: {sorted(widget_keys - params)}"
        )


# ComfyUI 的「连线输入」类型：由上游节点提供，没有 default 是正常且必须的
_LINK_TYPES = {"IMAGE", "AUDIO", "MASK", "LATENT", "MODEL", "CLIP", "VAE",
               "CONDITIONING", "CONTROL_NET", "SAMPLER", "SIGMAS", "NOISE"}


def test_a5_input_types_wellformed():
    """输入定义须为 (TYPE,) 简写 或 (TYPE, opts) 二元组；
    控件类型需有 default，连线类型（IMAGE/AUDIO 等）不应有 default。"""
    for key, cls in pkg.NODE_CLASS_MAPPINGS.items():
        it = cls.INPUT_TYPES()
        for group in ("required", "optional"):
            for name, spec in it.get(group, {}).items():
                if not isinstance(spec, tuple):
                    continue  # 少数情况为列表枚举（下拉框），跳过
                assert len(spec) in (1, 2), f"{key}.{name}: 输入定义长度异常 {len(spec)}"
                type_name = spec[0]
                if len(spec) == 1:
                    # (TYPE,) 简写：无配置项，仅允许连线类型
                    assert type_name in _LINK_TYPES, \
                        f"{key}.{name}: 控件类型不可省略 default"
                    continue
                opts = spec[1]
                assert isinstance(opts, (dict, list)), f"{key}.{name}: opts 类型异常"
                is_link = isinstance(type_name, str) and type_name in _LINK_TYPES
                if is_link:
                    assert "default" not in opts, f"{key}.{name}: 连线输入不应有 default"
                else:
                    assert "default" in opts, f"{key}.{name}: 控件缺 default"


# ===========================================================================
# B. 分块规划
# ===========================================================================
def _check_coverage(ch, total):
    assert ch[0][0] == 0 and ch[-1][1] == total, f"未覆盖 [0,{total}): {ch}"
    for s, e in ch:
        assert 0 <= s < e <= total, f"非法区间 ({s},{e}) total={total}"


def test_b1_long_video_overlap():
    ch = tc.plan_chunks(759, 128, 16)
    _check_coverage(ch, 759)
    assert len(ch) >= 5, f"分块过少: {len(ch)}"
    for i in range(1, len(ch)):
        assert ch[i - 1][1] - ch[i][0] == 16, f"重叠不符 @ {i}"


def test_b2_single_chunk():
    ch = tc.plan_chunks(20, 128, 16)
    assert ch == [(0, 20)], ch


def test_b3_overlap_zero():
    ch = tc.plan_chunks(100, 40, 0)
    _check_coverage(ch, 100)
    for i in range(1, len(ch)):
        assert ch[i - 1][1] == ch[i][0], "overlap=0 时相邻块应无缝相接"


def test_b4_degenerate_overlap_ge_chunk():
    """overlap >= chunk_size 时须被夹紧，且不产生空块、不死循环。"""
    ch = tc.plan_chunks(100, 8, 8)
    _check_coverage(ch, 100)
    for s, e in ch:
        assert s < e, f"出现空块 ({s},{e})"


def test_b5_no_infinite_loop_small():
    """历史 bug：e>=total 时 s=total-overlap 仍 <total 导致死循环。"""
    for total in (1, 5, 20, 21, 50):
        ch = tc.plan_chunks(total, 128, 16)
        assert ch, f"total={total} 无分块"
        _check_coverage(ch, total)


def test_b6_min_frames_enforced():
    """末块过短须并入前块（FlashVSR 要求 >= MIN_FRAMES）。"""
    ch = tc.plan_chunks(100, 30, 5)
    for s, e in ch:
        assert (e - s) >= MIN_FRAMES, f"块过短 {e-s} < {MIN_FRAMES}: {ch}"


def test_b7_chunk_size_one_collapses_by_min_frames():
    """chunk_size=1 属病态输入：每块(1帧)都 < MIN_FRAMES，应被末块合并逻辑
    折叠为单个覆盖区间，而非产生 10 个 1 帧块（后者会让 FlashVSR 直接报错）。"""
    ch = tc.plan_chunks(10, 1, 0)
    assert ch == [(0, 10)], ch
    _check_coverage(ch, 10)


def test_b8_exact_min_frames_total():
    ch = tc.plan_chunks(MIN_FRAMES, 128, 16)
    assert ch == [(0, MIN_FRAMES)], ch


# ===========================================================================
# C. FlashVSR 模块解析（P2.10：路径发现不触发重导入）
# ===========================================================================
def test_c1_peer_path_discovery():
    d = tc._ensure_flashvsr_on_path()
    assert d and os.path.isdir(d), f"未定位到 peer: {d}"
    assert os.path.exists(os.path.join(d, "AILab_FlashVSR.py")), f"缺 AILab_FlashVSR.py: {d}"


def test_c2_no_heavy_import_on_path_discovery():
    """P2.10 核心：路径发现不得把 AILab_FlashVSR 载入 sys.modules。"""
    assert "AILab_FlashVSR" not in sys.modules, \
        "P2.10 回归：_ensure_flashvsr_on_path 触发了真实模块导入"


def test_c3_import_flashvsr_callable_and_attrs():
    assert callable(tc.import_flashvsr)
    assert isinstance(tc._EFFICIENT_ATTRS, tuple) and len(tc._EFFICIENT_ATTRS) == 6
    assert tc._EFFICIENT_ATTRS == ("_setup_device_and_dtype", "init_pipe",
                                   "_pad_video_sequence", "_restore_video_sequence",
                                   "_tile", "_full")


def test_c4_peer_module_actually_imports():
    """**真实导入 peer 模块** —— 这是此前的覆盖盲区。

    历史 bug：peer 的 ``AILab_FlashVSR.py`` 内含 ``from .FlashVSR import ...``
    相对导入，只能以「包」的形式加载。旧实现三步都尝试顶层导入
    ``import_module("AILab_FlashVSR")``，必然抛
    ``ImportError: attempted relative import with no known parent package``。
    因为该异常被 ``except Exception`` 吞掉，离线测试又刻意不触发真实导入，
    这个缺陷直到真机运行才暴露。

    本测试把 ComfyUI 根目录加入 ``sys.path``（提供 folder_paths / comfy），
    完整走一遍 ``tc.import_flashvsr()``，确保集成路径真的通。
    """
    injected = []
    for p in (COMFY_ROOT, CUSTOM_NODES):
        if p and p not in sys.path:
            sys.path.insert(0, p)
            injected.append(p)
    try:
        try:
            import folder_paths  # noqa: F401  (ComfyUI 核心)
            import comfy.utils   # noqa: F401
        except Exception as e:   # noqa: BLE001
            raise _Skip(f"ComfyUI 运行时不可用（{type(e).__name__}: {e}）")

        mod = tc.import_flashvsr()
        assert mod is not None, "import_flashvsr() 返回 None"
        missing = [a for a in tc._EFFICIENT_ATTRS if not hasattr(mod, a)]
        assert not missing, f"peer 模块缺高效路径属性: {missing}"
        assert hasattr(mod, "AILab_FlashVSR"), "peer 模块缺 AILab_FlashVSR 类"
        assert hasattr(mod, "AILab_FlashVSR_Advanced"), \
            "peer 模块缺 AILab_FlashVSR_Advanced 类"
    finally:
        for p in injected:
            try:
                sys.path.remove(p)
            except ValueError:
                pass


def test_c5_peer_uses_relative_imports():
    """守卫测试：确认 peer 仍依赖相对导入（即 import_flashvsr 必须走包加载）。

    若 peer 哪天改成绝对导入，本测试会失败——届时可简化加载策略，
    但**当前**实现必须以目录名为包名加载，不能顶层导入。
    """
    peer_dir = tc._ensure_flashvsr_on_path()
    assert peer_dir, "未定位到 peer"
    src_path = os.path.join(peer_dir, "AILab_FlashVSR.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    import re
    assert re.search(r"^from\s+\.\w+", src, re.M), \
        "peer 不再使用相对导入，请复核 _load_peer_package / import_flashvsr 的加载策略"


# ===========================================================================
# D. ffmpeg 定位与视频 IO
# ===========================================================================
def test_d1_ffmpeg_located():
    ff = tc.get_ffmpeg()
    assert ff and os.path.exists(ff), ff


def test_d2_probe_frames_and_size():
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        src = make_test_video(os.path.join(tmp, "a.mp4"), 30, ff=ff)
        assert tc.probe_frames(src, ff) == 30
        w, h, fps = tc.probe_size(src, ff)
        assert (w, h) == (64, 64), (w, h)
        assert abs(fps - 1.0) < 1e-6, fps


def test_d3_read_write_roundtrip():
    """写入 N 帧再读回，帧数必须一致。"""
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "rt.mp4")
        frames = np.zeros((12, 32, 32, 3), dtype=np.uint8)
        frames[:, :, :, 0] = 200  # 非全黑，避免编码器优化
        tc.write_frames_ffmpeg(frames, 32, 32, 10.0, src, crf=19, ff=ff)
        got = tc.read_frames_ffmpeg(src, 0, 12, ff)
        assert got.shape == (12, 32, 32, 3), got.shape


# ===========================================================================
# E. 端到端流水线（帧数精确）
# ===========================================================================
def _e2e(tmp, n_frames, chunk_size, overlap, ff):
    src = make_test_video(os.path.join(tmp, "src.mp4"), n_frames, ff=ff)
    assert tc.probe_frames(src, ff) == n_frames
    w, h, fps = tc.probe_size(src, ff)
    chunks = tc.plan_chunks(n_frames, chunk_size, overlap)
    paths = []
    for i, (s, e) in enumerate(chunks):
        arr = tc.read_frames_ffmpeg(src, s, e, ff)
        assert arr.shape[0] == (e - s), (arr.shape, s, e)
        p = os.path.join(tmp, f"ck_{i:03d}.mp4")
        tc.write_frames_ffmpeg(arr, w, h, fps, p, crf=19, ff=ff)
        paths.append(p)
    out = os.path.join(tmp, "merged.mp4")
    tc.merge_chunk_videos(paths, out, overlap, source_audio_path=None,
                          fps=fps, crf=19, ff=ff)
    return tc.probe_frames(out, ff)


def test_e1_end_to_end_overlap8():
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        assert _e2e(tmp, 120, 40, 8, ff) == 120


def test_e2_end_to_end_overlap0():
    """overlap=0 时是纯切分拼接，帧数仍须精确。"""
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        assert _e2e(tmp, 120, 40, 0, ff) == 120


def test_e3_end_to_end_single_chunk():
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        assert _e2e(tmp, 30, 128, 16, ff) == 30


def test_e4_end_to_end_large_overlap():
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        assert _e2e(tmp, 90, 30, 12, ff) == 90


def test_e5_merge_reuses_source_audio():
    """带源音频路径时，输出须含音轨且帧数不变。"""
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        # 造一个带静音音轨的源
        src = os.path.join(tmp, "with_audio.mp4")
        subprocess.run(
            [ff, "-y", "-hide_banner", "-f", "lavfi",
             "-i", "testsrc=size=64x64:rate=1:duration=60",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
             "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264",
             "-c:a", "aac", src],
            check=True, capture_output=True,
        )
        w, h, fps = tc.probe_size(src, ff)
        chunks = tc.plan_chunks(60, 40, 8)
        paths = []
        for i, (s, e) in enumerate(chunks):
            arr = tc.read_frames_ffmpeg(src, s, e, ff)
            p = os.path.join(tmp, f"ca_{i}.mp4")
            tc.write_frames_ffmpeg(arr, w, h, fps, p, crf=19, ff=ff)
            paths.append(p)
        out = os.path.join(tmp, "merged_audio.mp4")
        tc.merge_chunk_videos(paths, out, 8, source_audio_path=src,
                              fps=fps, crf=19, ff=ff)
        assert tc.probe_frames(out, ff) == 60
        # 检查音轨存在
        r = subprocess.run([ff, "-hide_banner", "-i", out],
                           capture_output=True, text=True)
        assert "Audio:" in r.stderr, "输出视频缺少音轨"


# ===========================================================================
# F. 张量重建（Convention B）
# ===========================================================================
def _rebuild(total, chunk_size, overlap):
    """用「全局帧索引」填充假上采样张量，便于校验重建后是否有丢帧/重复。

    每块的值取 [s*3, e*3)，即携带全局帧号；若 Convention B 裁切正确，
    拼接结果应恰好等于 arange(0, total*3)。
    """
    import torch
    outs = []
    for s, e in tc.plan_chunks(total, chunk_size, overlap):
        L = e - s
        up = torch.arange(s * 3, e * 3, dtype=torch.float32).reshape(L, 3)
        outs.append(tc._trim_overlap(up, s, e, total, overlap))
    return torch.cat(outs, dim=0)


def test_f1_overlap8():
    assert _rebuild(120, 40, 8).shape[0] == 120


def test_f2_overlap0():
    assert _rebuild(120, 40, 0).shape[0] == 120


def test_f3_single_chunk():
    assert _rebuild(30, 128, 16).shape[0] == 30


def test_f4_no_duplicate_frames():
    """Convention B 下重建结果必须逐帧严格递增且恰好覆盖 0..total-1
    （无丢帧、无重复、无乱序）。"""
    import torch
    total = 120
    t = _rebuild(total, 40, 8)
    idx = t[:, 0]  # 每帧首元素 = 全局帧索引 * 3
    expected = torch.arange(total, dtype=torch.float32) * 3
    assert torch.equal(idx, expected), (
        f"重建序列与期望不符\n  实际: {idx[:30].tolist()}\n"
        f"  期望: {expected[:30].tolist()}"
    )


# ===========================================================================
# G. 参数构造与 fallback 映射
# ===========================================================================
_PARAM_KEYS = {"mode", "scale", "tiling", "ts", "to", "sr", "kvr", "lr",
               "cf", "ud", "tv", "seed", "device", "dtype"}


def test_g1_basic_params_all_presets():
    for preset in tc._BASIC_PRESETS:
        p = tc.basic_params(preset, 4, True, 1)
        assert set(p) == _PARAM_KEYS, f"{preset}: 键集不符 {set(p) ^ _PARAM_KEYS}"
        assert p["scale"] == 4 and p["seed"] == 1 and p["ud"] is True
        assert p["mode"] in tc._MODE_TO_BASIC, f"{preset}: mode={p['mode']} 无反查项"


def test_g2_advanced_params():
    for mv in tc._ADV_MODE_MAP:
        p = tc.advanced_params(mv, 4, True, 384, 24, 2.0, 2.0, 11,
                               True, True, True, "auto", "bf16", 1)
        assert set(p) == _PARAM_KEYS, f"{mv}: 键集不符"
        assert p["mode"] == tc._ADV_MODE_MAP[mv]


def test_g3_reverse_maps_are_inverses():
    assert tc._MODE_TO_MODEL_VERSION == {v: k for k, v in tc._ADV_MODE_MAP.items()}
    for mode, mv in tc._MODE_TO_MODEL_VERSION.items():
        assert tc._ADV_MODE_MAP[mv] == mode, f"反向映射不一致: {mode}<->{mv}"


def test_g4_upscale_kwargs_advanced():
    p = tc.advanced_params("Full (Best Quality)", 4, True, 384, 24, 2.0, 2.0,
                           11, True, True, True, "auto", "fp16", 7)
    kw = tc._upscale_kwargs(p, advanced=True)
    assert kw["model_version"] == "Full (Best Quality)"
    assert kw["precision"] == "fp16"          # fp16 直通
    assert kw["tile_size"] == 384 and kw["seed"] == 7
    assert set(kw) >= {"model_version", "scale", "enable_tiling", "tile_size",
                       "tile_overlap", "speed_optimization", "quality_boost",
                       "stability_level", "color_fix", "vae_tiling",
                       "unload_model", "device", "precision", "seed"}


def test_g5_upscale_kwargs_basic():
    for preset in tc._BASIC_PRESETS:
        p = tc.basic_params(preset, 4, True, 1)
        kw = tc._upscale_kwargs(p, advanced=False)
        assert kw["preset"] == tc._MODE_TO_BASIC[p["mode"]]
        assert set(kw) == {"preset", "scale", "unload_model", "seed"}


# ===========================================================================
# H. 错误处理与异常边界
# ===========================================================================
def test_h1_missing_src_video():
    try:
        pkg.NODE_CLASS_MAPPINGS["FlashVSR_Trunk"]().run(
            src_video="D:/__nonexistent__.mp4", preset="Balanced (2x Quality)",
            scale=4, chunk_size=128, overlap=16, unload_model=True, seed=1,
            output_name="x", fps=0.0, crf=19, output_dir="")
    except FileNotFoundError:
        return
    raise AssertionError("缺失源视频未抛 FileNotFoundError")


def test_h2_missing_chunk_dir():
    try:
        pkg.NODE_CLASS_MAPPINGS["FlashVSR_Trunk_Merge"]().merge(
            chunk_dir="D:/__nonexistent_dir__", name_pattern="*.mp4",
            overlap=16, output_name="o", crf=19, fps=0.0,
            output_dir="", source_video="")
    except FileNotFoundError:
        return
    raise AssertionError("缺失分块目录未抛 FileNotFoundError")


def test_h3_no_matching_chunks():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            pkg.NODE_CLASS_MAPPINGS["FlashVSR_Trunk_Merge"]().merge(
                chunk_dir=tmp, name_pattern="__nothing__*.mp4",
                overlap=16, output_name="o", crf=19, fps=0.0,
                output_dir="", source_video="")
        except FileNotFoundError:
            return
        raise AssertionError("无匹配分块未抛 FileNotFoundError")


def test_h4_short_video_rejected():
    """源视频帧数 < MIN_FRAMES 时 run_file_pipeline 须拒绝。"""
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        src = make_test_video(os.path.join(tmp, "short.mp4"), MIN_FRAMES - 1, ff=ff)
        try:
            tc.run_file_pipeline(src, tc.basic_params("Balanced (2x Quality)", 4,
                                                      True, 1), False,
                                 os.path.join(tmp, "out.mp4"),
                                 chunk_size=32, overlap=4, ff=ff,
                                 log=lambda *a: None)
        except ValueError:
            return
        raise AssertionError("过短视频未被拒绝")


def test_h5_short_tensor_rejected():
    import torch
    frames = torch.zeros((MIN_FRAMES - 1, 8, 8, 3), dtype=torch.float32)
    try:
        tc.upscale_frames_chunked(frames, tc.basic_params(
            "Balanced (2x Quality)", 4, True, 1), False, 32, 4)
    except ValueError:
        return
    raise AssertionError("过短张量未被拒绝")


def test_h6_ffmpeg_error_includes_stderr():
    """写视频失败时，异常信息须含 ffmpeg stderr（P1.4 要求）。"""
    ff = tc.get_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "no_such_dir", "out.mp4")
        frames = np.zeros((4, 16, 16, 3), dtype=np.uint8)
        try:
            tc.write_frames_ffmpeg(frames, 16, 16, 10.0, bad, crf=19, ff=ff)
        except RuntimeError as e:
            assert "ffmpeg rc=" in str(e), f"异常信息缺 rc: {e}"
            return
        raise AssertionError("写入非法路径未抛 RuntimeError")


# ===========================================================================
# I. 代码质量：type hints 完整性（P2.9）
# ===========================================================================
_PUBLIC_FNS = [
    "import_flashvsr", "_ensure_flashvsr_on_path", "plan_chunks", "_trim_overlap",
    "basic_params", "advanced_params", "_upscale_kwargs",
    "upscale_frames_chunked", "get_ffmpeg", "probe_frames", "probe_size",
    "read_frames_ffmpeg", "write_frames_ffmpeg",
    "merge_chunk_videos", "run_file_pipeline",
]


def test_i1_public_fns_have_return_hints():
    for name in _PUBLIC_FNS:
        fn = getattr(tc, name)
        hints = typing.get_type_hints(fn)
        assert "return" in hints, f"{name} 缺 return type hint"


def test_i2_pipe_methods_have_hints():
    for name in ("__init__", "upscale_chunk", "close"):
        fn = getattr(tc.FlashVSRPipe, name)
        hints = typing.get_type_hints(fn)
        assert "return" in hints, f"FlashVSRPipe.{name} 缺 return type hint"


def test_i3_warn_helper():
    """_warn 存在且可调用（清理路径告警通道）。"""
    assert callable(getattr(tc, "_warn", None))


# ===========================================================================
# J. 路径安全（P4#15：输出名防穿越 + 输出目录规范化）
# ===========================================================================
def test_j1_safe_output_name_plain():
    assert tc.safe_output_name("my_video", "fb") == "my_video"
    assert tc.safe_output_name("  spaced  ", "fb") == "spaced"


def test_j2_safe_output_name_blocks_traversal():
    """核心威胁：output_name 被拼进路径，绝不能带出目录语义。"""
    for evil in ("../../etc/passwd", "..\\..\\evil", "../../evil",
                 "....//evil", "a/../../b"):
        got = tc.safe_output_name(evil, "fb")
        assert "/" not in got and "\\" not in got and got != "..", \
            f"{evil!r} -> {got!r} 仍含路径语义"


def test_j3_safe_output_name_blocks_windows_drive():
    got = tc.safe_output_name("C:\\Windows\\System32\\evil", "fb")
    assert ":" not in got and "\\" not in got, got
    assert got == "evil", got


def test_j4_safe_output_name_strips_invalid_chars():
    got = tc.safe_output_name('a<b>c:"d"/e\\f|g?h*i', "fb")
    for ch in '<>:"\\/|?*':
        assert ch not in got, f"非法字符 {ch!r} 残留: {got!r}"


def test_j5_safe_output_name_fallback_and_length():
    assert tc.safe_output_name("", "fallback") == "fallback"
    assert tc.safe_output_name("   ", "fallback") == "fallback"
    assert tc.safe_output_name("..", "fallback") == "fallback"
    assert tc.safe_output_name(".", "fallback") == "fallback"
    long_name = "x" * 500
    assert len(tc.safe_output_name(long_name, "fb")) <= 120


def test_j6_output_stays_inside_dir():
    """端到端：即使用户给恶意 output_name，最终路径仍落在 out_dir 内。"""
    with tempfile.TemporaryDirectory() as out_dir:
        for evil in ("../../escaped", "..\\..\\escaped", "/abs/escaped"):
            name = tc.safe_output_name(evil, "safe")
            path = os.path.join(out_dir, f"{name}.mp4")
            assert os.path.dirname(os.path.abspath(path)) == \
                os.path.abspath(out_dir), f"{evil!r} 逃逸到 {path}"


def test_j7_resolve_output_dir_defaults_and_creates():
    with tempfile.TemporaryDirectory() as base:
        default_dir = os.path.join(base, "default")
        # 留空 -> 用 default_dir，且被创建
        got = tc.resolve_output_dir("", default_dir)
        assert os.path.isabs(got) and os.path.isdir(got)
        assert os.path.abspath(got) == os.path.abspath(default_dir)
        # 相对路径 -> 解析为绝对路径（相对 cwd）
        got2 = tc.resolve_output_dir("sub/dir", default_dir)
        assert os.path.isabs(got2) and os.path.isdir(got2)
        # ~ 展开
        got3 = tc.resolve_output_dir("~", default_dir)
        assert os.path.isabs(got3) and "~" not in got3


def test_j8_install_pip_failure_is_fatal():
    """P4#16：核心 pip 依赖失败必须抛错，而不是只 WARN 后继续。"""
    import importlib
    import types as _types

    spec = importlib.util.spec_from_file_location(
        "trunk_install", os.path.join(PKG_DIR, "install.py"))
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)

    # 用假的失败结果替换 run()，验证会抛 RuntimeError
    orig_run = inst.run
    try:
        inst.run = lambda cmd: _types.SimpleNamespace(returncode=1)
        req = os.path.join(PKG_DIR, "requirements.txt")
        assert os.path.exists(req), "requirements.txt 应存在"
        try:
            inst.pip_install_requirements(req)
        except RuntimeError as e:
            assert "imageio-ffmpeg" in str(e), f"错误信息应点名缺失依赖: {e}"
            return
        raise AssertionError("pip 失败未抛 RuntimeError（仍是静默 WARN）")
    finally:
        inst.run = orig_run


# ===========================================================================
# 独立运行器（无需 pytest）
# ===========================================================================
def _main():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed, failed, skipped = [], [], []
    for name, fn in tests:
        try:
            fn()
            passed.append(name)
            print(f"  PASS  {name}")
        except _Skip as e:
            skipped.append((name, e))
            print(f"  SKIP  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print("=" * 70)
    print(f"结果: PASS={len(passed)}  SKIP={len(skipped)}  "
          f"FAIL={len(failed)}  总计={len(tests)}")
    if failed:
        print("\n失败详情:")
        for name, e in failed:
            print(f"  - {name}: {type(e).__name__}: {e}")
        return 1
    print("ALL TESTS PASSED ✅" if not skipped else
          "所有可执行测试通过 ✅（部分因环境前置缺失被跳过）")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
