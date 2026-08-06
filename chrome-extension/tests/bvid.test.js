// 运行：node --test chrome-extension/tests/bvid.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { extractBvid } = require("../lib/bvid.js");

test("保留 BV 号 id 部分的大小写", () => {
  assert.equal(extractBvid("/video/BV12sbnzZEMD"), "BV12sbnzZEMD");
  assert.equal(extractBvid("/video/BV1GJ411k7qE/"), "BV1GJ411k7qE");
});

test("只规范化前缀（小写 bv 也归一为 BV）", () => {
  assert.equal(extractBvid("/video/bv12sbnzZEMD"), "BV12sbnzZEMD");
});

test("非播放页返回 null", () => {
  assert.equal(extractBvid("/video/"), null);
  assert.equal(extractBvid("/"), null);
  assert.equal(extractBvid(""), null);
  assert.equal(extractBvid("/list/ml123"), null);
});
