#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Comfy-FlashVSR-Trunk contributors
"""从 legacy/workflows/FlashVSR.json 生成 example_workflows/FlashVSR_Trunk_demo.json。

做法：保留 VHS_LoadVideo(31) / VHS_VideoCombine(40) / 超分节点(45)，
把节点 45 由 AILab_FlashVSR 换成 FlashVSR_Trunk_Frames（drop-in 替换），
并清理指向已删除节点的悬空连线。

用法（在仓库根目录执行）：
    python scripts/make_demo.py

升级 legacy 工作流或调整示例连线后，重跑本脚本即可同步示例文件，
避免手工编辑 JSON 出错。
"""
import json
import os

# 仓库根目录 = 本脚本所在目录的上一级
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KEEP_NODES = {31, 40, 45}          # VHS_LoadVideo, VHS_VideoCombine, FlashVSR_Trunk_Frames
KEEP_LINKS = {72, 74, 64, 65}      # 31->45 IMAGE, 31->45 AUDIO, 45->40 IMAGE, 45->40 AUDIO

# 节点 45 替换为 Trunk 后的默认控件值：preset / scale / chunk_size / overlap / unload / seed
WIDGET_VALUES_45 = ["Balanced (2x Quality)", 4, 128, 16, True, 1]


def main():
    src = os.path.join(WS, "legacy", "workflows", "FlashVSR.json")
    base = json.load(open(src, encoding="utf-8"))

    base["nodes"] = [n for n in base["nodes"] if n["id"] in KEEP_NODES]
    base["links"] = [l for l in base["links"] if l[0] in KEEP_LINKS]

    for n in base["nodes"]:
        if n["id"] == 45:
            n["type"] = "FlashVSR_Trunk_Frames"
            n["widgets_values"] = list(WIDGET_VALUES_45)
            p = n.setdefault("properties", {})
            p["cnr_id"] = "Comfy-FlashVSR-Trunk"
            p["Node name for S&R"] = "FlashVSR_Trunk_Frames"
            p["ver"] = "trunk-1.0"
            p.pop("ue_properties", None)      # 清理遗留的 ue 属性
        if n["id"] == 40:
            # frame_rate 原本由已删除的节点 28 通过 link 56 提供 -> 改为静态 24
            for inp in n.get("inputs", []):
                if inp.get("name") == "frame_rate":
                    inp["link"] = None
            wv = n.get("widgets_values")
            if isinstance(wv, dict):
                wv["frame_rate"] = 24.0
            if isinstance(wv, list) and wv:
                wv[0] = 24.0                  # VideoCombine 列表首项即 frame_rate

    # 清理指向已删除节点的悬空引用
    for n in base["nodes"]:
        for inp in n.get("inputs", []):
            lk = inp.get("link")
            if lk is not None and lk not in KEEP_LINKS:
                inp["link"] = None

    base["last_node_id"] = max(n["id"] for n in base["nodes"])
    base["last_link_id"] = max((l[0] for l in base["links"]), default=0)
    base["id"] = "flashvsr-trunk-demo"
    base["_comment"] = ("Drop-in demo: VHS_LoadVideo -> FlashVSR_Trunk_Frames "
                        "-> VHS_VideoCombine")

    outdir = os.path.join(WS, "example_workflows")
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, "FlashVSR_Trunk_demo.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(base, f, indent=2, ensure_ascii=False)
    print("wrote", out)

    # 自检：打印节点与连线，便于人工核对
    print("nodes:", [(n["id"], n["type"]) for n in base["nodes"]])
    print("links:", [(l[0], l[1], l[2], l[3], l[4]) for l in base["links"]])


if __name__ == "__main__":
    main()
