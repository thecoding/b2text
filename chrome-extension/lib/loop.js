// 多段循环播放的纯逻辑：选中若干句后，播放时间越过某句句尾时
// 返回下一句下标（最后一句之后回到第一句）。
// 同时兼容浏览器（content script 挂到 self.b2textLoop）和 Node（module.exports）。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.b2textLoop = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const EPSILON = 0.1;

  /**
   * 返回循环播放应该跳往的下一句下标；不需要跳则 -1。
   *
   * @param segments  按 start 升序的 [{start, end, ...}]
   * @param selected  升序排列的选中句下标数组
   * @param currentTime 当前播放时间（秒）
   * @param epsilon   句尾判定容差，默认 0.1
   *
   * 规则：
   * - 还没进入第一段 → -1（等播放自然进入，不抢控制权）
   * - 时间仍落在选中句内 → -1
   * - 越过选中句句尾（含间隙、手动拖到句后）→ 下一句；最后一句后回到第一句
   */
  function loopTarget(segments, selected, currentTime, epsilon = EPSILON) {
    if (!segments.length || !selected.length) return -1;
    // 二分找最后一个 start <= currentTime + epsilon 的选中句
    let lo = 0;
    let hi = selected.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (segments[selected[mid]].start <= currentTime + epsilon) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    const i = lo - 1;
    if (i < 0) return -1;
    if (currentTime >= segments[selected[i]].end - epsilon) {
      return selected[(i + 1) % selected.length];
    }
    return -1;
  }

  return { loopTarget, EPSILON };
});
