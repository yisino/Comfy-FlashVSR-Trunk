import json, os, subprocess, imageio_ffmpeg

WS = r"D:\AppData\WorkBuddy\2026-08-30-09-45-33"
OUTDIR = os.path.join(WS, "flashvsr_chunked")
os.makedirs(OUTDIR, exist_ok=True)

SRC = r"D:\Comfy-Desktop\ComfyUI-Shared\input\final_20260830_103305.mp4"
BASE = r"D:\Comfy-Desktop\ComfyUI-Installs\NVIDIA\ComfyUI\custom_nodes\ComfyUI-FlashVSR\example_workflows\FlashVSR.json"
FF = imageio_ffmpeg.get_ffmpeg_exe()
print("FFMPEG:", FF)

# ---- 1. 探测源视频 fps / 总帧数 / 分辨率 ----
def probe():
    p = subprocess.run([FF, "-hide_banner", "-i", SRC], capture_output=True, text=True)
    txt = p.stderr
    import re
    m_fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", txt)
    m_res = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    fps = float(m_fps.group(1)) if m_fps else 8.0
    w = int(m_res.group(1)) if m_res else 864
    h = int(m_res.group(2)) if m_res else 480
    # 用 ffprobe 风格精确取帧数（imageio-ffmpeg 不带 ffprobe，用 ffmpeg 数帧）
    cnt = subprocess.run([FF, "-hide_banner", "-i", SRC, "-map", "0:v:0", "-c", "copy",
                          "-f", "null", "-"], capture_output=True, text=True)
    m_nb = re.search(r"frame=\s*(\d+)", cnt.stderr)
    total = int(m_nb.group(1)) if m_nb else None
    return fps, w, h, total

fps, W, H, TOTAL = probe()
print(f"源视频: {W}x{H}, fps={fps}, 总帧数≈{TOTAL}")

# ---- 2. 计算 6 段分块（每段 127 帧，最后一段取余数） ----
CHUNK = 127
if TOTAL is None:
    TOTAL = 765  # 兜底（报错日志提示 759->765）
n_chunks = (TOTAL + CHUNK - 1) // CHUNK
print(f"分块数: {n_chunks}, 每段≈{CHUNK} 帧, 总覆盖={n_chunks*CHUNK} (源{TOTAL})")

chunks = []
for i in range(n_chunks):
    skip = i * CHUNK
    cap = min(CHUNK, TOTAL - skip)
    chunks.append((skip, cap))
print("chunks:", chunks)

# ---- 3. 读取基础工作流，剥离为 31->45->40 三段 ----
base = json.load(open(BASE))
keep_nodes = {31, 45, 40}
keep_links = {72, 74, 64, 65}
nodes = [n for n in base["nodes"] if n["id"] in keep_nodes]
links = [l for l in base["links"] if l[0] in keep_links]

for i, (skip, cap) in enumerate(chunks, start=1):
    wf = {
        "id": f"flashvsr-chunk-{i:02d}",
        "revision": 0,
        "last_node_id": 45,
        "last_link_id": 74,
        "nodes": [],
        "links": [list(l) for l in links],
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 1.0, "offset": [0, 0]},
                  "frontendVersion": "1.28.7",
                  "VHS_latentpreview": False, "VHS_latentpreviewrate": 0,
                  "VHS_MetadataImage": True, "VHS_KeepIntermediate": True},
        "version": 0.4,
    }
    for n in nodes:
        nn = json.loads(json.dumps(n))  # deep copy
        if n["id"] == 31:  # VHS_LoadVideo
            nn["widgets_values"]["video"] = "final_20260830_103305.mp4"
            nn["widgets_values"]["frame_load_cap"] = cap
            nn["widgets_values"]["skip_first_frames"] = skip
            nn["widgets_values"]["force_rate"] = 0
            nn["widgets_values"]["select_every_nth"] = 1
            # 同步 videopreview.params
            nn["widgets_values"]["videopreview"]["params"]["filename"] = "final_20260830_103305.mp4"
            nn["widgets_values"]["videopreview"]["params"]["frame_load_cap"] = cap
            nn["widgets_values"]["videopreview"]["params"]["skip_first_frames"] = skip
        elif n["id"] == 45:  # AILab_FlashVSR（保持 scale=4, unload_model=true）
            nn["widgets_values"] = ["Fast (2x Speed)", 4, True, 100, "fixed"]
        elif n["id"] == 40:  # VHS_VideoCombine
            nn["widgets_values"]["filename_prefix"] = f"FlashVSR_chunk{i:02d}"
            nn["widgets_values"]["frame_rate"] = fps
            nn["widgets_values"]["save_output"] = True
            nn["widgets_values"]["format"] = "video/h264-mp4"
            nn["widgets_values"]["crf"] = 19
            nn["widgets_values"]["pix_fmt"] = "yuv420p"
        wf["nodes"].append(nn)
    out = os.path.join(OUTDIR, f"FlashVSR_chunk_{i:02d}.json")
    json.dump(wf, open(out, "w"), indent=2, ensure_ascii=False)
    print(f"  写出 {out}  (skip={skip}, cap={cap})")

# ---- 4. 生成 ffmpeg 合并脚本（Windows .bat） ----
# 输出目录：ComfyUI 的 output（与 VHS 默认 save_output 一致）
OUT_MP4 = r"D:\Comfy-Desktop\ComfyUI-Shared\output"
list_txt = os.path.join(OUTDIR, "chunk_list.txt")
with open(list_txt, "w") as f:
    for i in range(1, n_chunks + 1):
        f.write(f"file 'FlashVSR_chunk{i:02d}_%05d.mp4'\n".replace("%05d", "") )  # 占位，下面用通配
# VHS 输出文件名形如 FlashVSR_chunk01_00001.mp4，用 ffmpeg concat 需精确名；改为 glob 收集
list_lines = []
for i in range(1, n_chunks + 1):
    list_lines.append(f"file 'FlashVSR_chunk{i:02d}_*.mp4'")  # ffmpeg concat 不支持通配，下面改方案
open(list_txt, "w").write("\n".join([f"file 'FlashVSR_chunk{i:02d}.mp4'" for i in range(1, n_chunks+1)]))

bat = os.path.join(OUTDIR, "merge_chunks.bat")
ff_quoted = FF.replace("\\", "\\\\")  # 给 bat 用原始路径
bat_content = f"""@echo off
REM FlashVSR 4x 分块合并脚本（自动复用 imageio-ffmpeg 内置 ffmpeg）
set "FF={FF}"
set "OUTDIR={OUT_MP4}"
set "SRC={SRC}"

REM 1) 收集各分块实际输出文件名（VHS 命名为 FlashVSR_chunkNN_xxxxx.mp4）
set "LIST={os.path.join(OUTDIR, 'chunk_list.txt')}"
(if exist "%LIST%" del "%LIST%")
for %%i in (01 02 03 04 05 06) do (
  for /f "delims=" %%f in ('dir /b "%OUTDIR%\\FlashVSR_chunk%%i_*.mp4" 2^>nul') do (
    echo file '%%f' >> "%LIST%"
  )
)

REM 2) 合并视频流，并复用原始音频（保证音画同步）
"%FF%" -hide_banner -y -f concat -safe 0 -i "%LIST%" ^
  -i "%SRC%" -map 0:v:0 -map 1:a? -c:v copy -c:a copy ^
  "%OUTDIR%\\FlashVSR_final_4x.mp4"

echo.
echo 合并完成: %OUTDIR%\\FlashVSR_final_4x.mp4
pause
"""
open(bat, "w").write(bat_content)
print("写出合并脚本:", bat)
print("FFMPEG 路径已记录, 可直接复用不必安装。")
