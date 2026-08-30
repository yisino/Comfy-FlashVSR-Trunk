#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FlashVSR 重叠分块 - 完成后自动合并 + 帧数校验 (后台轮询)。
判定完成: output 目录 6 段 FlashVSR_ovchunkNN_*.mp4 全部存在 且 ComfyUI 队列清空。
完成后: 用 venv python 跑 merge_overlap.py 合并, 再用 venv 的 imageio_ffmpeg 校验最终帧数 ~759。
结果写入同目录 watch_result.txt。
"""
import os, sys, time, glob, json, subprocess, urllib.request

COMFY = "http://127.0.0.1:8188"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = r"D:\Comfy-Desktop\ComfyUI-Shared\output"
VENV_PY = r"D:\Comfy-Desktop\ComfyUI-Installs\NVIDIA\ComfyUI\.venv\Scripts\python.exe"
N = 6
EXPECT_FRAMES = 759
RESULT = os.path.join(HERE, "watch_result.txt")


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(RESULT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_queue():
    try:
        d = json.load(urllib.request.urlopen(f"{COMFY}/queue", timeout=5))
        return len(d.get("queue_running", [])), len(d.get("queue_pending", []))
    except Exception:
        return None, None


def seg_files():
    res = {}
    for i in range(N):
        pat = os.path.join(OUT, f"FlashVSR_ovchunk{i+1:02d}_*.mp4")
        fs = sorted(glob.glob(pat))
        if fs:
            res[i + 1] = fs[-1]
    return res


def main():
    log("开始监控 6 段 FlashVSR 输出 (后台轮询)...")
    deadline = time.time() + 7200
    while time.time() < deadline:
        sf = seg_files()
        rn, pd = get_queue()
        log(f"已生成 {len(sf)}/6 段 | 队列 running={rn} pending={pd}")
        if len(sf) == N and (rn == 0 and pd == 0):
            time.sleep(15)  # 等落盘稳定
            if len(seg_files()) == N:
                break
        time.sleep(30)
    else:
        log("超时(2h): 6 段未全部完成, 停止。请检查 ComfyUI 队列/日志。")
        sys.exit(1)

    log("6 段全部完成, 触发合并 (merge_overlap.py)...")
    try:
        r = subprocess.run([VENV_PY, os.path.join(HERE, "merge_overlap.py")],
                           capture_output=True, text=True, timeout=1800)
        for l in r.stdout.strip().splitlines():
            log(l)
        if r.returncode != 0:
            log("合并失败! stderr:")
            for l in r.stderr.strip().splitlines()[:40]:
                log(l)
            sys.exit(1)
    except subprocess.TimeoutExpired:
        log("合并超时(30min)")
        sys.exit(1)

    final = os.path.join(OUT, "FlashVSR_final_2x_overlap.mp4")
    if not os.path.exists(final):
        log("最终文件未生成! 合并可能失败。")
        sys.exit(1)

    # 帧数校验 (venv python + imageio_ffmpeg)
    log("校验最终视频帧数...")
    chk = subprocess.run([VENV_PY, "-c",
        "import imageio_ffmpeg,subprocess,sys;"
        "FF=imageio_ffmpeg.get_ffmpeg_exe();"
        "r=subprocess.run([FF,'-hide_banner','-i',sys.argv[1],'-map','0:v:0','-c','copy','-f','null','-'],capture_output=True,text=True);"
        "last=[l for l in r.stderr.splitlines() if l.strip().startswith('frame=')];"
        "print(int(last[-1].split('=',1)[1].strip()) if last else 'NONE')", final],
        capture_output=True, text=True)
    fs = chk.stdout.strip()
    log(f"最终视频帧数: {fs} (期望 ~{EXPECT_FRAMES})")
    try:
        F = int(fs)
        if abs(F - EXPECT_FRAMES) <= 4:
            log(f"✅ 帧数校验通过 (偏差 {F - EXPECT_FRAMES})，无丢帧/错位。")
        else:
            log(f"⚠️ 帧数偏差较大 (期望 {EXPECT_FRAMES}, 实际 {F})，可能存在丢帧/错位，请人工核对。")
    except ValueError:
        log("⚠️ 无法解析帧数，请人工核对。")
    log("全部完成。")


if __name__ == "__main__":
    main()
