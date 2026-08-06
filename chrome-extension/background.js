// b2text extension — service worker（MV3）
//
// 职责：
//   1. 接收 content script / popup 消息：TRANSCRIBE_BVID / GET_TRANSCRIPT
//   2. 调用本地 b2text daemon：
//      - POST /transcribe           创建 bv 任务（output_dir 可省略，后端给默认值）
//      - GET  /tasks/{id}           查状态（queued/running/done/failed）
//      - GET  /tasks/{id}/segments  拿时间线
//   3. 按 tab 缓存任务与时间线（chrome.storage.session，SW 重启也不丢）
//   4. chrome.commands.onCommand → 向当前 tab 的 content script 转发跳句/开关命令

"use strict";

const DEFAULT_BACKEND = "http://127.0.0.1:8765";

const taskKey = (tabId) => `task:${tabId}`;
const timelineKey = (tabId) => `timeline:${tabId}`;

async function getSettings() {
  return chrome.storage.sync.get({
    backendUrl: DEFAULT_BACKEND,
    showTranscript: true,
    fontSize: 16,
    overlayColor: "#000000",
    overlayOpacity: 0.8,
  });
}

function normBase(url) {
  return (url || DEFAULT_BACKEND).trim().replace(/\/+$/, "");
}

async function requestJson(url, options = {}, timeoutMs = 15000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: ctrl.signal });
    let body = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    if (!res.ok) {
      const detail = body && body.detail ? JSON.stringify(body.detail) : "";
      throw new Error(`HTTP ${res.status} ${detail}`.trim());
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

async function submitTranscription(bvid, force = false) {
  const { backendUrl } = await getSettings();
  return requestJson(`${normBase(backendUrl)}/transcribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "bv", id: bvid, output_dir: null, force }),
  });
}

async function getTask(taskId) {
  const { backendUrl } = await getSettings();
  return requestJson(`${normBase(backendUrl)}/tasks/${encodeURIComponent(taskId)}`);
}

async function getSegments(taskId) {
  const { backendUrl } = await getSettings();
  return requestJson(`${normBase(backendUrl)}/tasks/${encodeURIComponent(taskId)}/segments`);
}

async function getCachedTask(tabId) {
  const k = taskKey(tabId);
  return (await chrome.storage.session.get(k))[k] || null;
}

async function setCachedTask(tabId, task) {
  await chrome.storage.session.set({ [taskKey(tabId)]: task });
}

function resolveTabId(msg, sender) {
  if (msg && msg.tabId != null) return msg.tabId;
  return sender && sender.tab ? sender.tab.id : null;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      const tabId = resolveTabId(msg, sender);
      switch (msg && msg.type) {
        case "TRANSCRIBE_BVID": {
          if (tabId == null) {
            sendResponse({ ok: false, error: "无法确定标签页" });
            return;
          }
          let task = await getCachedTask(tabId);
          if (
            !msg.force &&
            task &&
            task.bvid === msg.bvid &&
            task.status !== "failed" &&
            task.status !== "cancelled"
          ) {
            sendResponse({ ok: true, taskId: task.taskId, reused: true });
            return;
          }
          const body = await submitTranscription(msg.bvid, !!msg.force);
          if (!body.task_id) {
            // 结果文件已存在但库里没有对应任务：无需再解析
            sendResponse({ ok: true, reused: true });
            return;
          }
          task = { taskId: body.task_id, bvid: msg.bvid, status: "queued" };
          await setCachedTask(tabId, task);
          sendResponse({ ok: true, taskId: task.taskId, reused: false });
          return;
        }

        case "GET_TRANSCRIPT": {
          // content script 每 2s 轮询；没有任务时自动提交
          if (tabId == null) {
            sendResponse({ state: "idle" });
            return;
          }
          let task = await getCachedTask(tabId);
          if (
            !task ||
            task.bvid !== msg.bvid ||
            task.status === "failed" ||
            task.status === "cancelled"
          ) {
            // 失败/取消的旧任务不复用：页面重开或重试时自动重新解析
            const body = await submitTranscription(msg.bvid);
            if (!body.task_id) {
              // 已有结果文件（无任务记录）：直接按完成处理
              sendResponse({ state: "done", segments: [], duration: 0 });
              return;
            }
            task = { taskId: body.task_id, bvid: msg.bvid, status: "queued" };
            await setCachedTask(tabId, task);
            sendResponse({ state: "pending", taskId: task.taskId });
            return;
          }
          const job = await getTask(task.taskId);
          if (job.status === "done") {
            const tl = await getSegments(task.taskId);
            const timeline = {
              bvid: msg.bvid,
              segments: tl.segments || [],
              duration: tl.duration || 0,
            };
            await chrome.storage.session.set({ [timelineKey(tabId)]: timeline });
            await setCachedTask(tabId, { ...task, status: "done" });
            sendResponse({
              state: "done",
              taskId: task.taskId,
              segments: timeline.segments,
              duration: timeline.duration,
            });
          } else if (job.status === "failed" || job.status === "cancelled") {
            await setCachedTask(tabId, { ...task, status: job.status });
            sendResponse({ ok: false, state: "failed", error: job.error || job.status });
          } else {
            sendResponse({ state: "pending", taskId: task.taskId, status: job.status });
          }
          return;
        }

        case "GET_TIMELINE": {
          if (tabId == null) {
            sendResponse(null);
            return;
          }
          const tl =
            (await chrome.storage.session.get(timelineKey(tabId)))[timelineKey(tabId)] || null;
          sendResponse(tl);
          return;
        }

        default:
          sendResponse({ error: "unknown message type" });
      }
    } catch (e) {
      const message = e && e.message ? String(e.message) : String(e);
      sendResponse({
        ok: false,
        state: "failed",
        error: `无法连接本地后端（${message}）`,
      });
    }
  })();
  return true; // 异步 sendResponse
});

chrome.commands.onCommand.addListener(async (command) => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id == null) return;
  chrome.tabs.sendMessage(tab.id, { type: "COMMAND", command }).catch(() => {
    // 当前页没有 content script（不在播放页）时静默忽略
  });
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.get("backendUrl", (v) => {
    if (!v.backendUrl) {
      chrome.storage.sync.set({ backendUrl: DEFAULT_BACKEND });
    }
  });
});
