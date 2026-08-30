#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FlashVSR 重叠分块 - ComfyUI API 一键排队脚本。
前置: ComfyUI 已启动 (http://127.0.0.1:8188)。
功能: 将 flashvsr_overlap/FlashVSR_ovchunk_*.json (GUI 格式) 转为 /prompt 格式,
      逐个提交到 ComfyUI 队列, 6 段串行执行, 免去 GUI 手动加载 6 次。
跑完后运行 merge_overlap.bat 合并。
"""
import json, sys, os, glob, time, uuid, urllib.request, urllib.error

COMFY = "http://127.0.0.1:8188"
HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_WIDGETS = {"videopreview", "choose video to upload"}

# 仅 list 型 widgets_values 需要按 INPUT_TYPES 顺序映射的节点
# AILab_FlashVSR: required 顺序 preset, scale, unload_model, seed (frames/audio 为连接型)
LIST_WIDGET_ORDER = {
    "AILab_FlashVSR": ["preset", "scale", "unload_model", "seed"],
}


def gui_to_prompt(gui):
    """把 ComfyUI 前端保存的 workflow(GUI) 格式转成 /prompt 接受的 prompt 格式。"""
    link_map = {l[0]: (l[1], l[2]) for l in gui.get("links", [])}
    prompt = {}
    for n in gui.get("nodes", []):
        nid = str(n["id"])
        ctype = n["type"]
        inputs = {}
        # 1) 已连接的 input -> [源节点 id, 输出槽位]
        for inp in n.get("inputs", []):
            lk = inp.get("link")
            if lk is not None and lk in link_map:
                src, slot = link_map[lk]
                inputs[inp["name"]] = [str(src), slot]
        # 2) widget 值 (未连接的)
        wv = n.get("widgets_values")
        if isinstance(wv, dict):
            for k, v in wv.items():
                if k in SKIP_WIDGETS:
                    continue
                inputs.setdefault(k, v)
        elif isinstance(wv, list):
            order = LIST_WIDGET_ORDER.get(ctype, [])
            for i, name in enumerate(order):
                if name not in inputs and i < len(wv):
                    inputs[name] = wv[i]
        prompt[nid] = {"class_type": ctype, "inputs": inputs}
    return prompt


def wait_comfy(timeout_s=120):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            urllib.request.urlopen(f"{COMFY}/system_stats", timeout=3)
            return True
        except Exception:
            time.sleep(2)
    return False


def main():
    if not wait_comfy():
        print("ComfyUI 未在 8188 响应。请先启动 ComfyUI Desktop, 再运行本脚本。")
        sys.exit(1)
    client_id = str(uuid.uuid4())
    files = sorted(glob.glob(r"D:\Comfy-Desktop\ComfyUI-Shared\flashvsr_chunks\FlashVSR_ovchunk_*.json"))
    if not files:
        print("未找到 FlashVSR_ovchunk_*.json")
        sys.exit(1)
    print(f"ComfyUI 已连接, 准备提交 {len(files)} 段重叠工作流...")
    for f in files:
        gui = json.load(open(f, encoding="utf-8"))
        prompt = gui_to_prompt(gui)
        body = json.dumps({"prompt": prompt, "client_id": client_id}).encode()
        req = urllib.request.Request(
            f"{COMFY}/prompt", data=body,
            headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            r = json.loads(resp.read())
            print(f"[已排队] {os.path.basename(f)} -> prompt_id={r.get('prompt_id')}")
        except urllib.error.HTTPError as e:
            print(f"[失败] {os.path.basename(f)}: {e.read().decode('utf-8', 'ignore')[:600]}")
            sys.exit(1)
    print("全部提交完成。ComfyUI 将串行执行 6 段。跑完后运行 merge_overlap.bat 合并。")


if __name__ == "__main__":
    main()
