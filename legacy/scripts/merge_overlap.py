#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FlashVSR 2x 重叠分块合并脚本。
前置: 已用 flashvsr_overlap/FlashVSR_ovchunk_*.json 跑完 6 段 (输出在 ComfyUI-Shared/output)。
功能: 对每段输出视频裁掉重叠区(头/尾各 O 帧), 拼接, 复用源音频, 产出最终 2x 视频。
ffmpeg 复用 VHS 依赖的 imageio-ffmpeg 内置二进制, 无需另装。
"""
import os, sys, glob, subprocess

try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception as e:
    print("无法定位 ffmpeg (imageio_ffmpeg):", e)
    sys.exit(1)

OUT = r"D:\Comfy-Desktop\ComfyUI-Shared\output"
SRC = r"D:\Comfy-Desktop\ComfyUI-Shared\input\final_20260830_103305.mp4"
TMP = os.path.join(OUT, "_ov_tmp")
O = 16         # 必须与生成工作流时的重叠帧数一致 (chunk 边界: 0-135 / 119-262 / 246-389 ... 相邻重叠16帧)
N = 6

def probe_frames(p):
    """用 ffmpeg null 跑一遍拿到总帧数(末行 frame=NNN)。"""
    import re
    r = subprocess.run([FF, "-hide_banner", "-i", p, "-map", "0:v:0",
                        "-c", "copy", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r'frame=\s*(\d+)', r.stderr)
    return int(m[-1]) if m else None

def main():
    if not os.path.exists(SRC):
        print("源视频缺失:", SRC)
        sys.exit(1)
    os.makedirs(TMP, exist_ok=True)

    segs = []
    for i in range(N):
        pat = os.path.join(OUT, f"FlashVSR_ovchunk{i+1:02d}_*.mp4")
        fs = sorted(glob.glob(pat))
        if not fs:
            print(f"[错误] 找不到第 {i+1} 段输出: {pat}\n请先跑完 FlashVSR_ovchunk_{i+1:02d}.json")
            sys.exit(1)
        inp = fs[-1]
        F = probe_frames(inp)
        if F is None:
            print(f"[错误] 无法探测 {inp} 帧数")
            sys.exit(1)
        # 裁剪表达式: 每段丢弃重叠区, 边界落在相邻段重叠内 -> 视觉连续
        if i == 0:
            expr = f"lte(n,{F-1-O})"                 # 段1: 仅去尾 O(头无重叠)
        elif i == N - 1:
            expr = f"gte(n,{O})"                      # 段6: 仅去头 O(尾无重叠)
        else:
            expr = f"gte(n,{O})*lte(n,{F-1-O})"       # 中间段: 去头尾各 O
        outp = os.path.join(TMP, f"seg_{i+1:02d}.mp4")
        print(f"[trim] 段 {i+1}: 帧数 {F} -> 保留表达式 {expr}")
        subprocess.run([FF, "-hide_banner", "-i", inp,
                        "-vf", f"select='{expr}',setpts=N/FRAME_RATE/TB",
                        "-vsync", "0", "-r", "24",
                        "-pix_fmt", "yuv420p", "-crf", "19",
                        "-c:a", "copy", outp], check=True)
        segs.append(outp)

    listf = os.path.join(TMP, "list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{s}'\n")

    final = os.path.join(OUT, "FlashVSR_final_2x_overlap.mp4")
    print("[concat] 合并 + 复用源音频 ...")
    subprocess.run([FF, "-hide_banner", "-f", "concat", "-safe", "0", "-i", listf,
                    "-i", SRC,
                    "-c:v", "libx264", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest",
                    final], check=True)
    print("[完成] 输出:", final)

if __name__ == "__main__":
    main()
