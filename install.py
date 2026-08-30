#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Comfy-FlashVSR-Trunk contributors
"""
Comfy-FlashVSR-Trunk 安装脚本（由 ComfyUI Manager 克隆后自动执行）。

职责：
  1. 安装本插件自身的 pip 依赖（requirements.txt -> imageio-ffmpeg）。
  2. 自动安装 peer 依赖 ComfyUI-FlashVSR（若尚未存在）：
       - git clone 到同级 custom_nodes/ComfyUI-FlashVSR
       - 仅补装「轻量」依赖（einops/safetensors/tqdm/pillow/huggingface_hub），
         跳过 torch/torchvision/numpy —— 这些由 ComfyUI 运行环境自带，
         强行 pip 重装可能破坏 ComfyUI 的 venv。

失败策略（有意为之，非一刀切）：
  - 本插件 pip 依赖（imageio-ffmpeg）失败 -> **致命**，以非零码退出，
    让 ComfyUI Manager 识别为安装失败（缺 ffmpeg 则节点根本跑不起来）；
  - peer 依赖 ComfyUI-FlashVSR 克隆/装包失败 -> **仅告警**，不阻断
    （用户可自行用 Manager 或 git clone 补装，属于可恢复项）。
"""

import os
import subprocess
import sys


NODE_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOM_NODES_DIR = os.path.dirname(NODE_DIR)

PEER_NAME = "ComfyUI-FlashVSR"
PEER_REPO = "https://github.com/1038lab/ComfyUI-FlashVSR.git"

# ComfyUI-FlashVSR 需要的依赖。torch/torchvision/numpy 由 ComfyUI 提供，不在此安装。
PEER_SAFE_PIP = [
    "einops>=0.6.0",
    "safetensors>=0.4.0",
    "tqdm>=4.65.0",
    "pillow>=9.5.0",
    "huggingface_hub>=0.19.0",
]


def log(msg):
    print(f"[Comfy-FlashVSR-Trunk] {msg}", flush=True)


def run(cmd):
    log("> " + " ".join(cmd))
    return subprocess.run(cmd)


def pip_install_requirements(req_path):
    """安装本插件自身的 pip 依赖。

    **关键依赖**：失败即抛异常 —— ``imageio-ffmpeg`` 是 ``trunk_core.get_ffmpeg()``
    的 ffmpeg 来源，缺了它节点会在运行时才以「找不到 ffmpeg」的形式失败，
    排错成本高。宁可在安装阶段就明确报错（P4#16）。
    """
    if not os.path.exists(req_path):
        log(f"无 requirements 文件: {req_path}，跳过")
        return
    r = run([sys.executable, "-m", "pip", "install", "-r", req_path])
    if r.returncode != 0:
        raise RuntimeError(
            f"依赖安装失败（{req_path}，pip rc={r.returncode}）。\n"
            f"本插件依赖 imageio-ffmpeg 提供 ffmpeg 二进制，无法降级运行。\n"
            f"请手动执行后重试:\n"
            f"    {sys.executable} -m pip install -r {req_path}"
        )


def ensure_peer_dependency():
    peer_dir = os.path.join(CUSTOM_NODES_DIR, PEER_NAME)
    already = os.path.isdir(peer_dir) and os.path.exists(
        os.path.join(peer_dir, "AILab_FlashVSR.py")
    )
    if already:
        log(f"peer 依赖 '{PEER_NAME}' 已存在，跳过克隆")
        return

    log(f"自动安装 peer 依赖 '{PEER_NAME}'（clone 到 {peer_dir}）...")
    try:
        run(["git", "clone", PEER_REPO, peer_dir])
    except Exception as e:  # noqa: BLE001
        log(f"WARN: 自动克隆 '{PEER_NAME}' 失败: {e}")
        log(f"       请手动安装: cd {CUSTOM_NODES_DIR} && git clone {PEER_REPO}")
        return

    log(f"补装 '{PEER_NAME}' 的轻量依赖（跳过 torch 等 ComfyUI 自带项）...")
    try:
        run([sys.executable, "-m", "pip", "install"] + PEER_SAFE_PIP)
    except Exception as e:  # noqa: BLE001
        log(f"WARN: 安装 '{PEER_NAME}' 轻量依赖失败: {e}")


def main():
    """安装主流程。

    失败策略（有意为之，非一刀切）：
      - **本插件 pip 依赖**失败 -> 致命（缺 imageio-ffmpeg 则无法运行）
      - **peer 依赖 ComfyUI-FlashVSR** 失败 -> 仅告警（用户可自行用
        ComfyUI Manager 或 git clone 补装，不应阻断本插件安装）
    """
    log("开始安装...")
    pip_install_requirements(os.path.join(NODE_DIR, "requirements.txt"))
    ensure_peer_dependency()
    log("安装完成 ✅。请重启 ComfyUI，节点位于 🧪AILab/⚡FlashVSR/Trunk")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        # 关键步骤失败：给出明确错误并以非零码退出，让 ComfyUI Manager
        # 能识别为「安装失败」而不是静默成功。
        log(f"安装失败 ❌: {type(e).__name__}: {e}")
        sys.exit(1)
