// 运行：node --test chrome-extension/tests/loop.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loopTarget } = require("../lib/loop.js");

const segments = [
  { start: 0, end: 5 },
  { start: 5, end: 10 },
  { start: 12, end: 20 },
  { start: 30, end: 40 },
];

test("单段循环：越过句尾回到自身", () => {
  assert.equal(loopTarget(segments, [2], 19.95), 2);
  assert.equal(loopTarget(segments, [2], 20), 2);
  assert.equal(loopTarget(segments, [2], 21), 2);
});

test("段内不跳转", () => {
  assert.equal(loopTarget(segments, [0, 2], 3), -1);
  assert.equal(loopTarget(segments, [0, 2], 12), -1);
});

test("多段顺序：第一句结束跳第二句", () => {
  assert.equal(loopTarget(segments, [0, 2], 5), 2);
  assert.equal(loopTarget(segments, [0, 2], 4.95), 2);
});

test("多段顺序：句尾 epsilon 容差", () => {
  assert.equal(loopTarget(segments, [1, 3], 9.95), 3);
  assert.equal(loopTarget(segments, [1, 3], 10.05), 3);
});

test("最后一句结束回到第一句", () => {
  assert.equal(loopTarget(segments, [1, 3], 40), 1);
  assert.equal(loopTarget(segments, [1, 3], 41), 1);
});

test("间隙视为越过上一句句尾，跳下一选中句", () => {
  assert.equal(loopTarget(segments, [0, 2], 11), 2);
});

test("未进入第一段之前不跳转", () => {
  assert.equal(loopTarget(segments, [2], 5), -1);
  assert.equal(loopTarget(segments, [2], -1), -1);
});

test("手动拖到所有选中段之后跳回第一段", () => {
  assert.equal(loopTarget(segments, [0, 2], 100), 0);
});

test("非连续选择：在未选中句内也跳到下一选中段", () => {
  assert.equal(loopTarget(segments, [0, 3], 7), 3);
});

test("空选集 / 空时间线安全返回 -1", () => {
  assert.equal(loopTarget(segments, [], 5), -1);
  assert.equal(loopTarget([], [0], 5), -1);
  assert.equal(loopTarget([], [], 0), -1);
});
