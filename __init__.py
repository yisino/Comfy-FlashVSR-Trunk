#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Comfy-FlashVSR-Trunk contributors
"""
Comfy-FlashVSR-Trunk
====================
把 FlashVSR 的「长视频 4x 重叠分块超分 + 自动合并」流水线封装为 ComfyUI 扩展插件。

依赖：本插件复用已安装的 ComfyUI-FlashVSR（模型与推理代码），请先安装它。
"""

from .nodes import (
    FlashVSR_Trunk,
    FlashVSR_Trunk_Advanced,
    FlashVSR_Trunk_Frames,
    FlashVSR_Trunk_Merge,
)

NODE_CLASS_MAPPINGS = {
    "FlashVSR_Trunk": FlashVSR_Trunk,
    "FlashVSR_Trunk_Advanced": FlashVSR_Trunk_Advanced,
    "FlashVSR_Trunk_Frames": FlashVSR_Trunk_Frames,
    "FlashVSR_Trunk_Merge": FlashVSR_Trunk_Merge,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlashVSR_Trunk": "FlashVSR Trunk ⚡ (File Pipeline)",
    "FlashVSR_Trunk_Advanced": "FlashVSR Trunk ⚡ Advanced (File Pipeline)",
    "FlashVSR_Trunk_Frames": "FlashVSR Trunk ⚡ Frames (Drop-in)",
    "FlashVSR_Trunk_Merge": "FlashVSR Trunk ⚡ Merge Chunks",
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
