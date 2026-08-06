// b2text extension — 设置页
// chrome.storage.sync：backendUrl / showTranscript / fontSize / overlayEnabled /
// overlayColor / overlayOpacity。
"use strict";

const DEFAULTS = {
  backendUrl: "http://127.0.0.1:8765",
  showTranscript: true,
  fontSize: 16,
  overlayEnabled: false,
  overlayColor: "#000000",
  overlayOpacity: 0.8,
};

const $ = (id) => document.getElementById(id);

async function load() {
  const prefs = await chrome.storage.sync.get(DEFAULTS);
  $("backendUrl").value = prefs.backendUrl;
  $("showTranscript").checked = prefs.showTranscript;
  $("fontSize").value = prefs.fontSize;
  $("overlayEnabled").checked = prefs.overlayEnabled;
  $("overlayColor").value = prefs.overlayColor;
  $("overlayOpacity").value = prefs.overlayOpacity;
  $("opacityValue").textContent = prefs.overlayOpacity;
}

async function save() {
  const prefs = {
    backendUrl: ($("backendUrl").value || DEFAULTS.backendUrl).trim(),
    showTranscript: $("showTranscript").checked,
    fontSize: Math.min(32, Math.max(10, Number($("fontSize").value) || 16)),
    overlayEnabled: $("overlayEnabled").checked,
    overlayColor: $("overlayColor").value,
    overlayOpacity: Number($("overlayOpacity").value),
  };
  await chrome.storage.sync.set(prefs);
  const saved = $("saved");
  saved.textContent = "已保存";
  setTimeout(() => {
    saved.textContent = "";
  }, 1500);
}

document.querySelectorAll(".swatch").forEach((btn) => {
  btn.style.background = btn.dataset.color;
  btn.addEventListener("click", () => {
    $("overlayColor").value = btn.dataset.color;
  });
});

$("overlayOpacity").addEventListener("input", (e) => {
  $("opacityValue").textContent = e.target.value;
});

$("save").addEventListener("click", save);

load();
