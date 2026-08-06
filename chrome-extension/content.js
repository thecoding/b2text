// b2text extension — content script（注入 B 站播放页）
//
// 职责：
//   1. 从 URL 解析 bvid
//   2. 找到当前 <video> 播放器
//   3. 自动提交转写并轮询（消息走 background）
//   4. 快捷键跳句：上一句/下一句（<video>.currentTime）
//   5. 渲染可拖拽、可调字号的转写文字层
//   6. 渲染可拖拽、可缩放的遮挡层（颜色/透明度来自设置页）
//   7. 选句循环播放：勾选一句/多句后，循环播放选中句

(() => {
  "use strict";

  const seek = self.b2textSeek;
  const loop = self.b2textLoop;
  const POLL_MS = 2000;
  const WATCH_MS = 1000;

  const state = {
    bvid: null,
    video: null,
    segments: [],
    duration: 0,
    taskStatus: "idle", // idle | pending | done | failed
    selected: [], // 循环选集（升序下标）
    loopActive: false,
    panel: null,
    overlay: null,
    launchers: null,
    launcherT: null,
    launcherO: null,
    settings: {
      showTranscript: true,
      fontSize: 16,
      overlayEnabled: false,
      overlayColor: "#000000",
      overlayOpacity: 0.8,
    },
  };

  // ---------- 工具 ----------
  function getBvid() {
    return self.b2textBvid.extractBvid(location.pathname);
  }

  function findVideo() {
    const selectors = [
      ".bpx-player-container video",
      "#bilibili-player video",
      ".bpx-player-video-wrap video",
      "video",
    ];
    for (const sel of selectors) {
      const v = document.querySelector(sel);
      if (v && typeof v.duration === "number") return v;
    }
    return null;
  }

  function fmt(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const h = String(Math.floor(total / 3600)).padStart(2, "0");
    const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
    const s = String(total % 60).padStart(2, "0");
    return `${h}:${m}:${s}`;
  }

  // ---------- 设置 ----------
  async function loadSettings() {
    const prefs = await chrome.storage.sync.get({
      showTranscript: true,
      fontSize: 16,
      overlayEnabled: false,
      overlayColor: "#000000",
      overlayOpacity: 0.8,
    });
    Object.assign(state.settings, prefs);
    applySettings();
  }

  function createLauncher(text, title, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "b2text-launcher";
    btn.textContent = text;
    btn.title = title;
    btn.addEventListener("click", onClick);
    state.launchers.appendChild(btn);
    return btn;
  }

  function ensureLaunchers() {
    if (!state.launchers) {
      state.launchers = document.createElement("div");
      state.launchers.className = "b2text-launchers";
      document.documentElement.appendChild(state.launchers);
      state.launcherT = createLauncher("转写", "打开转写文字", () => toggleTranscript(true));
      state.launcherO = createLauncher("遮挡", "打开遮挡层", () => toggleOverlay(true));
    }
  }

  function applySettings() {
    ensureLaunchers();
    if (state.panel) {
      state.panel.style.display = state.settings.showTranscript ? "" : "none";
      state.panel.querySelector(".b2text-lines").style.fontSize =
        `${state.settings.fontSize}px`;
    }
    if (state.launcherT) {
      state.launcherT.style.display = state.settings.showTranscript ? "none" : "";
    }
    if (state.overlay) {
      state.overlay.style.background = state.settings.overlayColor;
      state.overlay.style.opacity = String(state.settings.overlayOpacity);
      state.overlay.style.display = state.settings.overlayEnabled ? "" : "none";
    }
    if (state.launcherO) {
      state.launcherO.style.display = state.settings.overlayEnabled ? "none" : "";
    }
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync") return;
    for (const key of Object.keys(state.settings)) {
      if (changes[key]) state.settings[key] = changes[key].newValue;
    }
    applySettings();
  });

  // ---------- 转写流程 ----------
  function setStatus(text) {
    if (state.panel) {
      const el = state.panel.querySelector(".b2text-status");
      if (el) el.textContent = text;
    }
    updateStartButton();
  }

  function updateStartButton() {
    const panel = state.panel;
    if (!panel || !panel.isConnected) return;
    const btn = panel.querySelector('button[data-act="start"]');
    if (!btn) return;
    const taskStatus = state.taskStatus;
    btn.textContent =
      taskStatus === "done" || taskStatus === "failed" ? "重新解析" : "开始解析";
  }

  function startTranscription() {
    if (state.taskStatus === "pending") return;
    resetTranscription(true);
  }

  function startPolling() {
    if (state._pollTimer) return;
    state._pollTimer = setInterval(() => {
      if (!state.bvid || state.taskStatus === "done" || state.taskStatus === "failed") {
        return;
      }
      chrome.runtime.sendMessage(
        { type: "GET_TRANSCRIPT", bvid: state.bvid },
        (resp) => {
          if (!resp) return;
          if (resp.state === "done") {
            state.taskStatus = "done";
            state.segments = resp.segments || [];
            state.duration = resp.duration || 0;
            renderTranscript();
            setStatus(`完成（${state.segments.length} 句）`);
            stopPolling();
          } else if (resp.ok === false) {
            state.taskStatus = "failed";
            setStatus("解析失败");
            showError(resp.error || "未知错误");
            stopPolling();
          } else {
            setStatus(resp.state === "pending" ? "解析中…" : "等待…");
          }
        }
      );
    }, POLL_MS);
  }

  function stopPolling() {
    if (state._pollTimer) {
      clearInterval(state._pollTimer);
      state._pollTimer = null;
    }
  }

  function resetTranscription(force = false) {
    stopPolling();
    state.taskStatus = "pending";
    state.segments = [];
    state.duration = 0;
    state.selected = [];
    state.loopActive = false;
    updateLoopButton();
    syncSelectionUI();
    setStatus("提交解析…");
    chrome.runtime.sendMessage(
      { type: "TRANSCRIBE_BVID", bvid: state.bvid, force },
      (resp) => {
        if (resp && resp.ok) {
          clearError();
          setStatus("解析中…");
          startPolling();
        } else {
          state.taskStatus = "failed";
          setStatus("提交失败");
          showError((resp && resp.error) || "无法连接后端");
        }
      }
    );
  }

  // ---------- 跳句 ----------
  function seekTo(index) {
    if (!state.video || index < 0 || index >= state.segments.length) return;
    state.video.currentTime = state.segments[index].start;
    highlight(index);
  }

  function jumpNext() {
    if (!state.video || !state.segments.length) return;
    const idx = seek.findNext(state.segments, state.video.currentTime);
    seekTo(idx);
  }

  function jumpPrev() {
    if (!state.video || !state.segments.length) return;
    const idx = seek.findPrev(state.segments, state.video.currentTime);
    seekTo(idx);
  }

  function highlight(index) {
    if (!state.panel) return;
    const lines = state.panel.querySelectorAll(".b2text-line");
    lines.forEach((line, i) => {
      line.classList.toggle("active", i === index);
    });
    if (index >= 0 && lines[index]) {
      lines[index].scrollIntoView({ block: "nearest" });
    }
  }

  // ---------- 选句循环 ----------
  function toggleSelect(index) {
    const pos = state.selected.indexOf(index);
    if (pos >= 0) {
      state.selected.splice(pos, 1);
      if (state.loopActive && !state.selected.length) {
        // 取消最后一勾时顺手关掉循环，避免“循环 0 句”的悬浮态
        state.loopActive = false;
        updateLoopButton();
      }
    } else {
      state.selected.push(index);
      state.selected.sort((a, b) => a - b);
    }
    syncSelectionUI();
  }

  function toggleLoop(force) {
    const next = force !== undefined ? force : !state.loopActive;
    if (next && !state.selected.length && state.segments.length) {
      // 没选句时默认循环当前播放句
      let idx = state.video
        ? seek.currentIndex(state.segments, state.video.currentTime)
        : -1;
      if (idx < 0) idx = 0;
      state.selected = [idx];
    }
    state.loopActive = next;
    updateLoopButton();
    syncSelectionUI();
  }

  function updateLoopButton() {
    const panel = state.panel;
    const btn = panel && panel.querySelector('button[data-act="loop"]');
    if (btn) btn.classList.toggle("on", state.loopActive);
  }

  function updateLoopStatus() {
    if (state.loopActive) {
      setStatus(`循环中（${state.selected.length} 句）`);
    } else if (state.taskStatus === "done") {
      setStatus(`完成（${state.segments.length} 句）`);
    }
  }

  function syncSelectionUI() {
    if (!state.panel) return;
    const lines = state.panel.querySelectorAll(".b2text-line");
    lines.forEach((line, i) => {
      const check = line.querySelector(".b2text-check");
      const selected = state.selected.includes(i);
      if (check) check.checked = selected;
      line.classList.toggle("selected", selected);
    });
    updateLoopStatus();
  }

  // ---------- 转写文字层 ----------
  function ensurePanel() {
    if (state.panel) return state.panel;
    const panel = document.createElement("div");
    panel.className = "b2text-panel";
    panel.innerHTML = `
      <div class="b2text-panel-header">
        <span class="b2text-title">b2text 转写</span>
        <span class="b2text-status"></span>
        <button type="button" data-act="start" title="提交当前视频解析">开始解析</button>
        <button type="button" data-act="font-down" title="减小字号">A−</button>
        <button type="button" data-act="font-up" title="增大字号">A+</button>
        <button type="button" data-act="loop" title="循环播放选中句（Ctrl/^+Shift+L）">循环</button>
        <button type="button" data-act="close" title="隐藏（Ctrl/^+Shift+T 可再打开）">×</button>
      </div>
      <div class="b2text-lines"></div>
      <div class="b2text-error" hidden>
        <button type="button" data-act="copy-error" title="复制错误信息">复制</button>
        <pre class="b2text-error-text"></pre>
      </div>
    `;
    document.documentElement.appendChild(panel);
    state.panel = panel;
    setupPanelDrag(panel);
    panel.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === "start") startTranscription();
      else if (act === "font-down") adjustFontSize(-2);
      else if (act === "font-up") adjustFontSize(2);
      else if (act === "loop") toggleLoop();
      else if (act === "close") toggleTranscript(false);
      else if (act === "copy-error") copyError();
    });
    panel.querySelector(".b2text-lines").addEventListener("click", (e) => {
      const check = e.target.closest(".b2text-check");
      if (check) {
        toggleSelect(Number(check.dataset.index));
        return;
      }
      const line = e.target.closest(".b2text-line");
      if (line && line.dataset.index != null) seekTo(Number(line.dataset.index));
    });
    return panel;
  }

  function clearError() {
    if (!state.panel) return;
    const box = state.panel.querySelector(".b2text-error");
    if (box) box.hidden = true;
  }

  function showError(message) {
    const panel = ensurePanel();
    const box = panel.querySelector(".b2text-error");
    const text = panel.querySelector(".b2text-error-text");
    if (!box || !text) return;
    text.textContent = message || "未知错误";
    box.hidden = false;
  }

  function copyError() {
    const panel = state.panel;
    const text = panel && panel.querySelector(".b2text-error-text");
    if (!text || !text.textContent) return;
    const btn = panel.querySelector('[data-act="copy-error"]');
    const done = () => {
      if (btn) {
        btn.textContent = "已复制";
        setTimeout(() => {
          if (btn) btn.textContent = "复制";
        }, 1500);
      }
    };
    const msg = text.textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(msg).then(done).catch(() => {});
    } else {
      const ta = document.createElement("textarea");
      ta.value = msg;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        // 复制失败时静默
      }
      ta.remove();
      done();
    }
  }

  function setupPanelDrag(panel) {
    const header = panel.querySelector(".b2text-panel-header");
    let drag = null;
    header.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button") || e.button !== 0) return;
      const rect = panel.getBoundingClientRect();
      drag = {
        dx: e.clientX - rect.left,
        dy: e.clientY - rect.top,
      };
      // 捕获要挂在监听 move/up 的元素（header）上，否则事件不再经过它
      header.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    header.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const x = Math.min(
        Math.max(0, e.clientX - drag.dx),
        window.innerWidth - 60
      );
      const y = Math.min(
        Math.max(0, e.clientY - drag.dy),
        window.innerHeight - 40
      );
      panel.style.left = `${x}px`;
      panel.style.top = `${y}px`;
      panel.style.right = "auto";
    });
    header.addEventListener("pointerup", () => {
      drag = null;
    });
    header.addEventListener("pointercancel", () => {
      drag = null;
    });
  }

  function adjustFontSize(delta) {
    const next = Math.min(32, Math.max(10, state.settings.fontSize + delta));
    state.settings.fontSize = next;
    chrome.storage.sync.set({ fontSize: next });
    applySettings();
  }

  function renderTranscript() {
    const panel = ensurePanel();
    const lines = panel.querySelector(".b2text-lines");
    lines.textContent = "";
    state.segments.forEach((seg, i) => {
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "b2text-check";
      check.dataset.index = String(i);
      const row = document.createElement("div");
      row.className = "b2text-line";
      row.dataset.index = String(i);
      const time = document.createElement("span");
      time.className = "b2text-time";
      time.textContent = fmt(seg.start);
      const spk = document.createElement("span");
      spk.className = "b2text-speaker";
      spk.textContent = seg.speaker;
      const text = document.createElement("span");
      text.className = "b2text-text";
      text.textContent = seg.text;
      row.append(check, time, spk, text);
      lines.appendChild(row);
    });
    syncSelectionUI();
    applySettings();
  }

  function toggleTranscript(visible) {
    state.settings.showTranscript =
      visible !== undefined ? visible : !state.settings.showTranscript;
    chrome.storage.sync.set({ showTranscript: state.settings.showTranscript });
    applySettings();
  }

  // ---------- 遮挡层 ----------
  function ensureOverlay() {
    if (state.overlay && state.overlay.isConnected) return state.overlay;
    if (!state.video) return null;
    const host = state.video.parentElement || document.body;
    if (getComputedStyle(host).position === "static") {
      host.style.position = "relative";
    }
    const overlay = document.createElement("div");
    overlay.className = "b2text-overlay";
    overlay.innerHTML = `
      <div class="b2text-overlay-toolbar">
        <span>遮挡</span>
        <button type="button" data-act="ov-close" title="关闭遮挡（Ctrl/^+Shift+O 再打开）">×</button>
      </div>
      <div class="b2text-overlay-resize" title="拖动右下角缩放"></div>
    `;
    host.appendChild(overlay);
    state.overlay = overlay;
    setupOverlayDrag(overlay);
    setupOverlayResize(overlay);
    overlay
      .querySelector('[data-act="ov-close"]')
      .addEventListener("click", () => toggleOverlay(false));
    applySettings();
    return overlay;
  }

  function setupOverlayDrag(overlay) {
    let drag = null;
    // 整个遮挡条都可以拖动（排除关闭按钮和缩放把手）
    overlay.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      if (e.target.closest("button") || e.target.closest(".b2text-overlay-resize")) return;
      const host = overlay.parentElement;
      const rect = overlay.getBoundingClientRect();
      const hostRect = host ? host.getBoundingClientRect() : { left: 0, top: 0 };
      drag = {
        dx: e.clientX - rect.left,
        dy: e.clientY - rect.top,
        hostLeft: hostRect.left,
        hostTop: hostRect.top,
      };
      overlay.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    overlay.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const host = overlay.parentElement;
      const hw = host ? host.clientWidth : window.innerWidth;
      const hh = host ? host.clientHeight : window.innerHeight;
      const w = overlay.offsetWidth;
      const h = overlay.offsetHeight;
      const left = e.clientX - drag.dx - drag.hostLeft;
      const top = e.clientY - drag.dy - drag.hostTop;
      overlay.style.left = `${Math.min(Math.max(0, left), Math.max(0, hw - w))}px`;
      overlay.style.top = `${Math.min(Math.max(0, top), Math.max(0, hh - h))}px`;
    });
    overlay.addEventListener("pointerup", () => {
      drag = null;
    });
    overlay.addEventListener("pointercancel", () => {
      drag = null;
    });
  }

  function setupOverlayResize(overlay) {
    const handle = overlay.querySelector(".b2text-overlay-resize");
    let drag = null;
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      const host = overlay.parentElement;
      const rect = overlay.getBoundingClientRect();
      const hostRect = host ? host.getBoundingClientRect() : { left: 0, top: 0 };
      drag = {
        x: e.clientX,
        y: e.clientY,
        w: overlay.offsetWidth,
        h: overlay.offsetHeight,
        left: rect.left - hostRect.left,
        bottom: rect.bottom - hostRect.top, // 底边锚定：缩放时保持底边不动
        hostW: host ? host.clientWidth : window.innerWidth,
      };
      handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const newH = Math.min(drag.bottom, Math.max(40, drag.h + (e.clientY - drag.y)));
      const newW = Math.min(drag.hostW - drag.left, Math.max(160, drag.w + (e.clientX - drag.x)));
      overlay.style.top = `${drag.bottom - newH}px`;
      overlay.style.height = `${newH}px`;
      overlay.style.left = `${drag.left}px`;
      overlay.style.width = `${newW}px`;
    });
    handle.addEventListener("pointerup", () => {
      drag = null;
    });
    handle.addEventListener("pointercancel", () => {
      drag = null;
    });
  }

  function toggleOverlay(visible) {
    const next = visible !== undefined ? visible : !state.settings.overlayEnabled;
    state.settings.overlayEnabled = next;
    chrome.storage.sync.set({ overlayEnabled: next });
    if (next) {
      ensureOverlay();
    }
    applySettings();
  }

  // ---------- 命令与消息 ----------
  function handleCommand(command) {
    switch (command) {
      case "next-sentence":
        jumpNext();
        break;
      case "prev-sentence":
        jumpPrev();
        break;
      case "toggle-transcript":
        toggleTranscript();
        break;
      case "toggle-overlay":
        toggleOverlay();
        break;
    }
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg) return;
    if (msg.type === "COMMAND") {
      handleCommand(msg.command);
      sendResponse({ ok: true });
    } else if (msg.type === "GET_PAGE_STATE") {
      sendResponse({
        bvid: state.bvid,
        taskStatus: state.taskStatus,
        segmentCount: state.segments.length,
      });
    } else if (msg.type === "RESET") {
      resetTranscription(true);
      sendResponse({ ok: true });
    }
  });

  // ---------- 主循环：检测页面/播放器变化 ----------
  function watch() {
    const bvid = getBvid();
    if (bvid && bvid !== state.bvid) {
      stopPolling();
      state.bvid = bvid;
      state.taskStatus = "idle";
      state.segments = [];
      state.duration = 0;
      state.selected = [];
      state.loopActive = false;
      updateLoopButton();
      syncSelectionUI();
      clearError();
      ensurePanel();
      setStatus("识别到 " + bvid + "，点击「开始解析」");
    }
    if (!state.video || !state.video.isConnected) {
      state.video = findVideo();
      if (state.video) {
        state.video.addEventListener("timeupdate", () => {
          if (!state.segments.length) return;
          const idx = seek.currentIndex(state.segments, state.video.currentTime);
          highlight(idx);
          if (state.loopActive) {
            const next = loop.loopTarget(
              state.segments,
              state.selected,
              state.video.currentTime
            );
            if (next >= 0) {
              state.video.currentTime = state.segments[next].start;
              highlight(next);
            }
          }
        });
        state.video.addEventListener("ended", () => {
          if (!state.loopActive || !state.selected.length) return;
          const first = state.selected[0];
          state.video.currentTime = state.segments[first].start;
          state.video.play().catch(() => {});
        });
        // 视频就绪后，如果设置里默认开遮挡层，则补建遮挡层
        if (state.settings.overlayEnabled) {
          ensureOverlay();
          applySettings();
        }
      }
    }
  }

  function init() {
    loadSettings();
    ensurePanel();
    setStatus("等待视频…");
    watch();
    setInterval(watch, WATCH_MS);
    // 循环快捷键由扩展内监听实现：Chrome 限制单个扩展最多 4 个
    // chrome.commands 默认快捷键，选句循环不占用该配额。
    document.addEventListener("keydown", (e) => {
      if (!e.ctrlKey || !e.shiftKey || e.altKey || e.metaKey) return;
      if (e.key.toLowerCase() !== "l") return;
      const t = e.target;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      toggleLoop();
    });
  }

  init();
})();
