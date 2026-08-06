// b2text extension — 工具栏弹窗
// 显示当前播放页 bvid 与解析状态；可强制重新解析；跳转设置页。
"use strict";

(async () => {
  const bvidEl = document.getElementById("bvid");
  const statusEl = document.getElementById("status");
  const btn = document.getElementById("retranscribe");

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = (tab && tab.url) || "";
  const m = url.match(/\/video\/(BV[\w]+)/i);

  if (!m) {
    bvidEl.textContent = "—";
    statusEl.textContent = "请先打开一个 B 站视频播放页";
    return;
  }

  // 只规范化前缀，id 部分保留原样（B 站 BV 号区分大小写）
  const bvid = "BV" + m[1].slice(2);
  bvidEl.textContent = bvid;

  try {
    const resp = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_STATE" });
    statusEl.textContent = describe(resp);
    btn.textContent =
      resp && resp.taskStatus === "idle" ? "开始解析" : "重新解析";
    btn.disabled = false;
  } catch {
    statusEl.textContent = "扩展脚本未加载，刷新页面重试";
    btn.disabled = true;
  }

  function describe(resp) {
    switch (resp && resp.taskStatus) {
      case "done":
        return `完成（${resp.segmentCount} 句）`;
      case "pending":
        return "解析中…";
      case "failed":
        return "解析失败";
      default:
        return "等待中…";
    }
  }

  btn.addEventListener("click", async () => {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: "RESET" });
      statusEl.textContent = "已重新提交";
    } catch {
      statusEl.textContent = "页面未响应，请刷新后重试";
    }
  });

  document.getElementById("open-options").addEventListener("click", (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });
})();
