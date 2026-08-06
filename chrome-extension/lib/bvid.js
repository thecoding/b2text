// BV 号解析：只规范化前缀为 BV，id 部分保留原样。
// B 站 BV 号 id 是区分大小写的 base58 编码，整体 toUpperCase 会 404（啥都木有）。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.b2textBvid = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /**
   * 从播放页 pathname 提取 BV 号。
   * 例："/video/BV12sbnzZEMD/" → "BV12sbnzZEMD"（保留 id 大小写）
   * 非播放页返回 null。
   */
  function extractBvid(pathname) {
    const m = String(pathname || "").match(/\/video\/(BV[\w]+)\/?/i);
    return m ? "BV" + m[1].slice(2) : null;
  }

  return { extractBvid };
});
