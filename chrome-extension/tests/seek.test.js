// 运行：node --test chrome-extension/tests/seek.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { findNext, findPrev, currentIndex } = require("../lib/seek.js");

const segments = [
  { start: 0, end: 5 },
  { start: 5, end: 10 },
  { start: 12, end: 20 },
  { start: 30, end: 40 },
];

test("findNext 返回当前句的下一句", () => {
  assert.equal(findNext(segments, 0), 1);
  assert.equal(findNext(segments, 4.95), 1); // 句尾仍算本句
  assert.equal(findNext(segments, 6), 2);
  assert.equal(findNext(segments, 20), 3);
  assert.equal(findNext(segments, 41), -1);  // 已到最后一句
});

test("findNext 在句子间隙跳到下一句", () => {
  assert.equal(findNext(segments, 11), 2);   // 10~12 之间是间隙
});

test("findNext 未到第一句时跳第一句", () => {
  assert.equal(findNext(segments, -5), 0);
});

test("findPrev 句子里直接跳到上一句句首", () => {
  assert.equal(findPrev(segments, 41), 3);
  assert.equal(findPrev(segments, 31), 2);   // 第 4 句中间 → 第 3 句
  assert.equal(findPrev(segments, 30), 2);   // 第 4 句句首 → 第 3 句
  assert.equal(findPrev(segments, 15), 1);   // 第 3 句中间 → 第 2 句
  assert.equal(findPrev(segments, 6), 0);    // 第 2 句中间 → 第 1 句
  assert.equal(findPrev(segments, 5), 0);
  assert.equal(findPrev(segments, 0), -1);   // 第一句句首没有更早的
});

test("findPrev 在句子间隙回到最后一句句首", () => {
  assert.equal(findPrev(segments, 11), 1);
  assert.equal(findPrev(segments, 21), 2);
});

test("findNext 用 epsilon 忽略浮点抖动", () => {
  assert.equal(findNext(segments, 5.05), 2);
});

test("currentIndex 高亮当前句", () => {
  assert.equal(currentIndex(segments, 0), 0);
  assert.equal(currentIndex(segments, 4.9), 0);
  assert.equal(currentIndex(segments, 5), 1);
  assert.equal(currentIndex(segments, 15), 2);
  assert.equal(currentIndex(segments, -1), -1);
});

test("空时间线安全返回", () => {
  assert.equal(findNext([], 0), -1);
  assert.equal(findPrev([], 0), -1);
  assert.equal(currentIndex([], 0), -1);
});
