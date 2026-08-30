#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FlashVSR 重叠分块工作流生成器 (参数化, 对任意源视频一键出整套分块)。
功能:
  1. 用 imageio_ffmpeg 探测源视频总帧数;
  2. 按 [段数 N, 重叠帧 O] 计算均匀分块边界 (相邻重叠 O 帧, 保证覆盖全部帧);
  3. 以 FlashVSR_ovchunk_01.json 为模板, 生成 N 个 ovchunk 工作流
     (改 VHS_LoadVideo 的 video/skip_first_frames/frame_load_cap, AILab_FlashVSR 的 scale,
      VHS_VideoCombine 的 filename_prefix);
  4. 同步更新 merge_overlap.py 与 watch_and_merge.py 里写死的 SRC / O / N / EXPECT_FRAMES。

依赖: ComfyUI venv 的 imageio_ffmpeg (用于探测帧数)。请用 gen_chunks.bat 或 venv python 运行。
用法:
  gen_chunks.py --src "D:/ComfyUI-Shared/input/video.mp4" [--segments 6] [--overlap 16] [--scale 2]
注意: 源视频需放在 ComfyUI-Shared/input/ (VHS_LoadVideo 按文件名在 input 目录查找)。
"""
import os, sys, json, re, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "FlashVSR_ovchunk_01.json")
MERGE_PY = os.path.join(HERE, "merge_overlap.py")
WATCH_PY = os.path.join(HERE, "watch_and_merge.py")


def probe_frames(p):
    import imageio_ffmpeg, re
    FF = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([FF, "-hide_banner", "-i", p, "-map", "0:v:0",
                        "-c", "copy", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r'frame=\s*(\d+)', r.stderr)
    return int(m[-1]) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="源视频绝对路径 (缺省则交互输入)")
    ap.add_argument("--segments", type=int, default=6, help="分块段数 (默认 6)")
    ap.add_argument("--overlap", type=int, default=16, help="相邻段重叠帧数 (默认 16)")
    ap.add_argument("--scale", type=int, default=2, choices=[2, 4], help="放大倍数 2/4 (默认 2)")
    args = ap.parse_args()

    src = os.path.abspath(args.src) if args.src else os.path.abspath(input("源视频绝对路径: ").strip().strip('"'))
    if not os.path.exists(src):
        print("源视频不存在:", src)
        sys.exit(1)
    N, O, SCALE = args.segments, args.overlap, args.scale

    total = probe_frames(src)
    if total is None:
        print("无法探测帧数 (确认 imageio_ffmpeg 可用)")
        sys.exit(1)
    if total < 21:
        print(f"视频仅 {total} 帧, FlashVSR 需要至少 21 帧")
        sys.exit(1)
    print(f"源视频帧数: {total}")

    # 均匀分块: 每段目标长 L, 段 i 起始 s_i = i*(L-O), 末段补齐
    L = (total + (N - 1) * O + N - 1) // N  # ceil((total+(N-1)*O)/N)
    chunks = []
    for i in range(N):
        s = i * (L - O)
        ln = min(L, total - s)
        chunks.append((s, ln))
    end = chunks[-1][0] + chunks[-1][1]
    print(f"分块参数: 段长 L={L}, 末段结束帧={end - 1} (期望={total - 1})")
    if end < total:
        print("⚠️ 警告: 未完全覆盖源视频末尾, 请检查参数")

    if not os.path.exists(TEMPLATE):
        print("模板缺失:", TEMPLATE)
        sys.exit(1)
    tpl = json.load(open(TEMPLATE, encoding="utf-8"))

    for i, (s, ln) in enumerate(chunks):
        g = json.loads(json.dumps(tpl))  # 深拷贝模板
        for n in g["nodes"]:
            if n["type"] == "VHS_LoadVideo":
                w = n["widgets_values"]
                w["video"] = os.path.basename(src)
                w["skip_first_frames"] = s
                w["frame_load_cap"] = ln
            elif n["type"] == "AILab_FlashVSR":
                n["widgets_values"][1] = SCALE
            elif n["type"] == "VHS_VideoCombine":
                n["widgets_values"]["filename_prefix"] = f"FlashVSR_ovchunk{i + 1:02d}_"
        OUT_CHUNKS = r"D:\Comfy-Desktop\ComfyUI-Shared\flashvsr_chunks"
        os.makedirs(OUT_CHUNKS, exist_ok=True)
        outp = os.path.join(OUT_CHUNKS, f"FlashVSR_ovchunk{i + 1:02d}.json")
        json.dump(g, open(outp, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
        print(f"  段{i + 1}: skip={s:>4} cap={ln:>4}")

    # 更新 merge_overlap.py (SRC 整行替换, O/N/EXPECT 仅替换数字以保留注释)
    mt = open(MERGE_PY, encoding="utf-8").read()
    mt = re.sub(r'(?m)^SRC = .*', lambda m: f'SRC = r"{src}"', mt)
    mt = re.sub(r'(?m)^O = \d+', f'O = {O}', mt)
    mt = re.sub(r'(?m)^N = \d+', f'N = {N}', mt)
    mt = re.sub(r'(?m)^EXPECT_FRAMES = \d+', f'EXPECT_FRAMES = {total}', mt)
    open(MERGE_PY, "w", encoding="utf-8").write(mt)
    print(f"已更新 merge_overlap.py: SRC/O/N/EXPECT_FRAMES")

    # 更新 watch_and_merge.py
    wt = open(WATCH_PY, encoding="utf-8").read()
    wt = re.sub(r'(?m)^N = \d+', f'N = {N}', wt)
    wt = re.sub(r'(?m)^EXPECT_FRAMES = \d+', f'EXPECT_FRAMES = {total}', wt)
    open(WATCH_PY, "w", encoding="utf-8").write(wt)
    print(f"已更新 watch_and_merge.py: N/EXPECT_FRAMES")

    print()
    print("✅ 整套分块工作流已生成。请确保源视频在 ComfyUI-Shared/input/。")
    print("下一步: 启动 ComfyUI -> 双击 queue_overlap.bat 排队 -> 跑完双击 merge_overlap.bat 合并")


if __name__ == "__main__":
    main()
