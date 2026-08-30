#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comfy-FlashVSR-Trunk 安装脚本（由 ComfyUI Manager 克隆后自动执行）。

职责：
  1. 安装本插件自身的 pip 依赖（requirements.txt -> imageio-ffmpeg）。
  2. 自动安装 peer 依赖 ComfyUI-FlashVSR（若尚未存在）：
       - git clone 到同级 custom_nodes/ComfyUI-FlashVSR
       - 仅补装「轻量」依赖（einops/safetensors/tqdm/pillow/huggingface_hub），
         跳过 torch/torchvision/numpy —— 这些由 ComfyUI 运行环境自带，
         强行 pip 重装可能破坏 ComfyUI 的 venv。

任何单步失败都不会中断整体安装，只会打印 WARN 并继续，
确保「一键安装」在异常情况下也能尽量完成。
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
    if not os.path.exists(req_path):
        log(f"无 requirements 文件: {req_path}，跳过")
        return
    try:
        run([sys.executable, "-m", "pip", "install", "-r", req_path])
    except Exception as e:  # noqa: BLE001
        log(f"WARN: pip 安装 {req_path} 失败（可稍后手动重试）: {e}")


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
    log("开始安装...")
    pip_install_requirements(os.path.join(NODE_DIR, "requirements.txt"))
    ensure_peer_dependency()
    log("安装完成 ✅。请重启 ComfyUI，节点位于 🧪AILab/⚡FlashVSR/Trunk")


if __name__ == "__main__":
    main()
