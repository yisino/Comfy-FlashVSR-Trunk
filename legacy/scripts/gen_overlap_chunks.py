import json, os

WORK = r"D:\AppData\WorkBuddy\2026-08-30-09-45-33\flashvsr_overlap"
SRC_WF = r"D:\AppData\WorkBuddy\2026-08-30-09-45-33\flashvsr_chunked\FlashVSR_chunk_01.json"
os.makedirs(WORK, exist_ok=True)

base = json.load(open(SRC_WF, encoding="utf-8"))

# 源视频实测: 864x480 / 24fps / 759 帧
TOTAL = 759
L = 127          # 单段有效长度(无重叠时)
O = 8            # 重叠帧数(每段首尾各叠 O 帧)
# 无重叠段边界
seg_starts = [0, 127, 254, 381, 508, 635]
seg_ends   = [126, 253, 380, 507, 634, 758]

print("重叠窗口 (O=%d):" % O)
for i in range(6):
    wf = json.loads(json.dumps(base))  # 深拷贝
    ls = max(0, seg_starts[i] - O)
    le = min(TOTAL - 1, seg_ends[i] + O)
    cap = le - ls + 1
    skip = ls
    # 节点 31: 加载重叠窗口
    n31 = [n for n in wf["nodes"] if n["id"] == 31][0]
    n31["widgets_values"]["frame_load_cap"] = cap
    n31["widgets_values"]["skip_first_frames"] = skip
    n31["widgets_values"]["videopreview"]["params"]["frame_load_cap"] = cap
    n31["widgets_values"]["videopreview"]["params"]["skip_first_frames"] = skip
    # 节点 40: 输出前缀(与硬切版区分)
    n40 = [n for n in wf["nodes"] if n["id"] == 40][0]
    n40["widgets_values"]["filename_prefix"] = f"FlashVSR_ovchunk{i+1:02d}"
    wf["id"] = f"flashvsr-ovchunk-{i+1:02d}"
    out = os.path.join(WORK, f"FlashVSR_ovchunk_{i+1:02d}.json")
    json.dump(wf, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"  chunk {i+1}: 加载帧 [{ls},{le}]  cap={cap}  skip={skip}  -> {os.path.basename(out)}")
print("生成完成, 目录:", WORK)
