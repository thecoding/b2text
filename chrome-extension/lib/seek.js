// 句子跳转的纯逻辑：二分查找上一句/下一句/当前句。
// 同时兼容浏览器（content script 挂到 self.b2textSeek）和 Node（module.exports）。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.b2textSeek = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // segments: [{start, end, ...}]，按 start 升序
  const EPSILON = 0.1;

  /** 包含 currentTime 的句子下标；落在所有句子之前则 -1。 */
  function currentIndex(segments, currentTime) {
    let lo = 0;
    let hi = segments.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (segments[mid].start <= currentTime) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    return lo - 1;
  }

  /** 下一句：当前句的下一句；还没到第一句则跳第一句；没有则 -1。 */
  function findNext(segments, currentTime) {
    if (!segments.length) return -1;
    const idx = currentIndex(segments, currentTime);
    if (idx < 0) return 0;
    return idx + 1 < segments.length ? idx + 1 : -1;
  }

  /**
   * 上一句：直接跳到上一句的句首。
   * - 在句子里：跳到上一句句首（第二句中间按上一句 → 第一句句首）
   * - 在句子间隙：回到最后一句的句首
   * - 第一句之前：-1（不跳）
   */
  function findPrev(segments, currentTime) {
    if (!segments.length) return -1;
    const idx = currentIndex(segments, currentTime);
    if (idx < 0) return -1;
    const inGap = currentTime > segments[idx].end;
    const target = inGap ? idx : idx - 1;
    return target >= 0 ? target : -1;
  }

  return { findNext, findPrev, currentIndex, EPSILON };
});
