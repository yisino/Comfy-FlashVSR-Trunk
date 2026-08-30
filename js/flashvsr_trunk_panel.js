// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Comfy-FlashVSR-Trunk contributors
/*
 * FlashVSR-Trunk · 实时展示面板（前端）
 * =====================================
 * 通过 ComfyUI WebSocket 监听后端 ProgressReporter 推送的
 * `flashvsr_trunk_progress` 消息，渲染一个浮动状态面板：
 *   1) 分块数量：总数 / 已完成 / 失败
 *   2) 渲染进度：百分比 + 进度条 + 当前帧 / 总帧数
 *   3) 已渲染时长：HH:mm:ss 实时计时 + 预计剩余(ETA)
 *   4) 视频对比区：超分前 / 超分后 左右并列，播放/暂停/拖动同步
 * 无渲染任务时默认隐藏（顶栏 ✕ 也可手动收起）。
 */

import { app } from "../../scripts/app.js";

const WS_TYPE = "flashvsr_trunk_progress";
const ROUTE = "/flashvsr_trunk_video";

let panel = null;
let els = null;
const st = {
  runId: null,
  timer: null,
  startMs: 0,
  last: null,
};

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------
function fmtHMS(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(h)}:${p(m)}:${p(s)}`;
}

function videoURL(abs) {
  if (!abs) return "";
  return `${location.protocol}//${location.host}${ROUTE}?f=${encodeURIComponent(abs)}`;
}

function statusMeta(status) {
  switch (status) {
    case "running":   return { label: "渲染中", cls: "fvt-badge-running" };
    case "done":      return { label: "已完成", cls: "fvt-badge-done" };
    case "failed":    return { label: "失败",   cls: "fvt-badge-failed" };
    case "cancelled": return { label: "已取消", cls: "fvt-badge-cancelled" };
    default:          return { label: "空闲",   cls: "fvt-badge-idle" };
  }
}

// ---------------------------------------------------------------------------
// 面板构建（单例）
// ---------------------------------------------------------------------------
function ensurePanel() {
  if (panel) return;

  const style = document.createElement("style");
  style.textContent = `
#flashvsr_trunk_panel {
  position: fixed; top: 50px; right: 12px; width: 384px; z-index: 9999;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
               Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 13px; color: var(--fg-color, #e6e6e6);
  background: var(--comfy-input-bg, #202022);
  border: 1px solid var(--border-color, #3a3a3a);
  border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,0.55);
  overflow: hidden; user-select: none;
}
#flashvsr_trunk_panel.fvt-hidden { display: none; }
.fvt-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px;
  background: linear-gradient(90deg, rgba(74,158,255,0.20), rgba(74,158,255,0.04));
  border-bottom: 1px solid var(--border-color, #3a3a3a);
}
.fvt-title { font-weight: 600; letter-spacing: .3px; }
.fvt-close {
  background: transparent; border: none; color: var(--fg-color, #e6e6e6);
  cursor: pointer; font-size: 14px; line-height: 1; padding: 2px 6px; border-radius: 6px;
}
.fvt-close:hover { background: rgba(255,255,255,0.10); }
.fvt-body { padding: 10px 12px; }
.fvt-status { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.fvt-badge {
  font-size: 11px; font-weight: 600; padding: 2px 9px; border-radius: 999px; white-space: nowrap;
}
.fvt-badge-idle       { background: #3a3a3a; color: #bbbbbb; }
.fvt-badge-running    { background: rgba(74,158,255,0.20); color: #8ec5ff; }
.fvt-badge-done       { background: rgba(63,185,80,0.20);  color: #7ee787; }
.fvt-badge-failed     { background: rgba(248,81,73,0.22);  color: #ff9b94; }
.fvt-badge-cancelled  { background: rgba(240,136,62,0.22); color: #ffb886; }
.fvt-msg {
  font-size: 11px; color: var(--descrip-text, #9a9a9a);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;
}
.fvt-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 8px; }
.fvt-cell {
  background: rgba(255,255,255,0.04); border: 1px solid var(--border-color, #3a3a3a);
  border-radius: 8px; padding: 6px 8px; min-width: 0;
}
.fvt-k { font-size: 10px; color: var(--descrip-text, #9a9a9a); margin-bottom: 2px;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fvt-v { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fvt-bar { height: 8px; background: rgba(255,255,255,0.08); border-radius: 999px;
           overflow: hidden; margin-bottom: 10px; }
.fvt-bar-fill { height: 100%; width: 0%; background: linear-gradient(90deg,#4a9eff,#7ee787);
                transition: width .3s ease; }
.fvt-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.fvt-vidwrap { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.fvt-vlabel { font-size: 11px; color: var(--descrip-text, #9a9a9a);
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fvt-vidwrap video {
  width: 100%; border-radius: 8px; background: #000; border: 1px solid var(--border-color, #3a3a3a);
  aspect-ratio: 16 / 9; object-fit: contain;
}
.fvt-actions { margin-top: 10px; display: flex; justify-content: flex-end; }
.fvt-cancel {
  background: rgba(248,81,73,0.15); color: #ff9b94; border: 1px solid rgba(248,81,73,0.4);
  border-radius: 8px; padding: 6px 12px; cursor: pointer; font-size: 12px; font-weight: 600;
}
.fvt-cancel:hover:not(:disabled) { background: rgba(248,81,73,0.28); }
.fvt-cancel:disabled { opacity: .4; cursor: default; }
`;
  document.head.appendChild(style);

  panel = document.createElement("div");
  panel.id = "flashvsr_trunk_panel";
  panel.className = "fvt-hidden";
  panel.innerHTML = `
    <div class="fvt-head">
      <span class="fvt-title">⚡ FlashVSR Trunk · 实时状态</span>
      <button class="fvt-close" id="fvt-close" title="隐藏面板">✕</button>
    </div>
    <div class="fvt-body">
      <div class="fvt-status">
        <span class="fvt-badge fvt-badge-idle" id="fvt-badge">空闲</span>
        <span class="fvt-msg" id="fvt-msg"></span>
      </div>
      <div class="fvt-grid">
        <div class="fvt-cell"><div class="fvt-k">分块（完成/总/失败）</div><div class="fvt-v" id="fvt-chunks">- / -</div></div>
        <div class="fvt-cell"><div class="fvt-k">进度</div><div class="fvt-v" id="fvt-pct">0%</div></div>
        <div class="fvt-cell"><div class="fvt-k">帧（当前/总）</div><div class="fvt-v" id="fvt-frames">0 / 0</div></div>
        <div class="fvt-cell"><div class="fvt-k">已渲染</div><div class="fvt-v" id="fvt-elapsed">00:00:00</div></div>
        <div class="fvt-cell"><div class="fvt-k">剩余(估)</div><div class="fvt-v" id="fvt-eta">--:--:--</div></div>
        <div class="fvt-cell"><div class="fvt-k">分辨率</div><div class="fvt-v" id="fvt-res">- / -</div></div>
      </div>
      <div class="fvt-bar"><div class="fvt-bar-fill" id="fvt-bar"></div></div>
      <div class="fvt-compare" id="fvt-compare">
        <div class="fvt-vidwrap" id="fvt-wrap-before">
          <div class="fvt-vlabel" id="fvt-lbl-before">超分前</div>
          <video id="fvt-before" controls preload="metadata"></video>
        </div>
        <div class="fvt-vidwrap" id="fvt-wrap-after">
          <div class="fvt-vlabel" id="fvt-lbl-after">超分后</div>
          <video id="fvt-after" controls preload="metadata"></video>
        </div>
      </div>
      <div class="fvt-actions">
        <button class="fvt-cancel" id="fvt-cancel">取消渲染</button>
      </div>
    </div>`;
  document.body.appendChild(panel);

  els = {
    badge:    panel.querySelector("#fvt-badge"),
    msg:      panel.querySelector("#fvt-msg"),
    chunks:   panel.querySelector("#fvt-chunks"),
    pct:      panel.querySelector("#fvt-pct"),
    frames:   panel.querySelector("#fvt-frames"),
    elapsed:  panel.querySelector("#fvt-elapsed"),
    eta:      panel.querySelector("#fvt-eta"),
    res:      panel.querySelector("#fvt-res"),
    bar:      panel.querySelector("#fvt-bar"),
    compare:  panel.querySelector("#fvt-compare"),
    lblBefore:panel.querySelector("#fvt-lbl-before"),
    lblAfter: panel.querySelector("#fvt-lbl-after"),
    wrapBefore: panel.querySelector("#fvt-wrap-before"),
    wrapAfter:  panel.querySelector("#fvt-wrap-after"),
    before:   panel.querySelector("#fvt-before"),
    after:    panel.querySelector("#fvt-after"),
    cancel:   panel.querySelector("#fvt-cancel"),
    close:    panel.querySelector("#fvt-close"),
  };

  // 两个视频：播放/暂停/拖动进度 互相同步
  bindSync(els.before, els.after);
  bindSync(els.after, els.before);

  els.cancel.addEventListener("click", () => {
    try { app.api.interrupt(); } catch (e) { /* 忽略 */ }
  });
  els.close.addEventListener("click", () => {
    panel.classList.add("fvt-hidden");
    stopTimer();
  });
}

function bindSync(a, b) {
  let lock = false;
  a.addEventListener("play", () => { if (!lock) { lock = true; b.play().catch(() => {}); lock = false; } });
  a.addEventListener("pause", () => { if (!lock) { lock = true; b.pause(); lock = false; } });
  a.addEventListener("seeked", () => { if (!lock) { lock = true; try { b.currentTime = a.currentTime; } catch (e) {} lock = false; } });
}

function startTimer() {
  stopTimer();
  st.timer = setInterval(() => {
    const d = st.last;
    if (!d) return;
    const elapsed = (Date.now() - st.startMs) / 1000;
    els.elapsed.textContent = fmtHMS(elapsed);
    if (d.total_frames > 0 && d.processed_frames > 0 && d.processed_frames < d.total_frames) {
      const frac = d.processed_frames / d.total_frames;
      els.eta.textContent = fmtHMS(elapsed * (1 - frac) / frac);
    }
  }, 1000);
}

function stopTimer() {
  if (st.timer) { clearInterval(st.timer); st.timer = null; }
}

function showPanel() {
  ensurePanel();
  panel.classList.remove("fvt-hidden");
}

// ---------------------------------------------------------------------------
// 数据应用
// ---------------------------------------------------------------------------
function applyData(d) {
  ensurePanel();

  // 新的一次运行：重置本地计时起点与视频
  if (d.run_id !== st.runId) {
    st.runId = d.run_id;
    st.startMs = Date.now() - (d.elapsed_s || 0) * 1000;
    [els.before, els.after].forEach((v) => {
      v.removeAttribute("src");
      v.removeAttribute("data-src");
      try { v.load(); } catch (e) {}
    });
  }
  st.last = d;
  showPanel();

  // 状态徽标
  const meta = statusMeta(d.status);
  els.badge.textContent = meta.label;
  els.badge.className = "fvt-badge " + meta.cls;
  els.msg.textContent = d.message || "";

  // 分块数量
  els.chunks.textContent =
    `已完成 ${d.done_chunks}/${d.total_chunks}` +
    (d.failed_chunks ? ` · 失败 ${d.failed_chunks}` : "");

  // 渲染进度（优先按帧数，回退按分块数）
  let pct = 0;
  if (d.total_frames > 0) pct = (d.processed_frames / d.total_frames) * 100;
  else if (d.total_chunks > 0) pct = (d.done_chunks / d.total_chunks) * 100;
  pct = Math.max(0, Math.min(100, pct));
  els.pct.textContent = pct.toFixed(0) + "%";
  els.bar.style.width = pct + "%";

  // 帧
  els.frames.textContent = `${d.processed_frames} / ${d.total_frames}`;

  // 已渲染时长 + ETA
  els.elapsed.textContent = fmtHMS(d.elapsed_s);
  if (d.status === "running" && d.eta_s != null) els.eta.textContent = fmtHMS(d.eta_s);
  else els.eta.textContent = "--:--:--";

  // 分辨率
  els.res.textContent = `${d.src_res || "-"} → ${d.out_res || "-"}`;

  // 视频对比区
  const hasBefore = !!d.video_before;
  const hasAfter = !!d.video_after;
  if (hasBefore || hasAfter) {
    els.compare.style.display = "";
    if (hasBefore) {
      els.wrapBefore.style.display = "";
      els.lblBefore.textContent = `超分前 (${d.src_res || "源分辨率"})`;
      if (els.before.getAttribute("data-src") !== d.video_before) {
        els.before.setAttribute("data-src", d.video_before);
        els.before.src = videoURL(d.video_before);
        try { els.before.load(); } catch (e) {}
      }
    } else {
      els.wrapBefore.style.display = "none";
    }
    if (hasAfter) {
      els.wrapAfter.style.display = "";
      els.lblAfter.textContent = `超分后 (${d.out_res || "输出分辨率"})`;
      if (els.after.getAttribute("data-src") !== d.video_after) {
        els.after.setAttribute("data-src", d.video_after);
        els.after.src = videoURL(d.video_after);
        try { els.after.load(); } catch (e) {}
      }
    } else {
      els.wrapAfter.style.display = "none";
    }
  } else {
    els.compare.style.display = "none";
  }

  // 取消按钮仅渲染中可点
  els.cancel.disabled = (d.status !== "running");

  if (d.status === "running") startTimer();
  else stopTimer();
}

// ---------------------------------------------------------------------------
// 扩展注册
// ---------------------------------------------------------------------------
app.registerExtension({
  name: "Comfy.FlashVSRTrunk.Panel",
  async setup() {
    ensurePanel();
    const api = app.api;
    if (api && typeof api.addEventListener === "function") {
      api.addEventListener(WS_TYPE, (e) => {
        // 兼容不同 ComfyUI 版本的派发格式：detail 可能是 data 本身，也可能包一层
        let payload = (e && e.detail) ? e.detail : (e || {});
        if (payload && payload.data && payload.data.status) payload = payload.data;
        if (payload && payload.status) applyData(payload);
      });
    }
  },
});
