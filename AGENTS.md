# AGENTS.md — b2text 仓库约定

> 本文件是给 AI 编码代理（Codex / Claude Code / Cursor 等）的仓库级指令。
> 进入仓库工作前请完整阅读并遵守；提交代码时尤其必须遵守「提交规范」。

## 项目概览

b2text：把 B 站视频对话转成带说话人标签的文本，完全本地运行。

- `b2text/`：Python 后端（FastAPI daemon + SQLite job queue + FunASR worker + CLI）
- `chrome-extension/`：配套 Chrome 扩展（MV3，看视频时按句跳转）
- `tests/`：pytest 单元测试；`chrome-extension/tests/`：node 单测
- 语言：代码注释与提交信息以中文为主

## 测试

本机 macOS 上 pytest 可能因 readline/libedit 段错误，用以下命令绕开：

```bash
./venv/bin/python -m pytest -p no:cacheprovider -p no:capture -m "not integration"
node --test chrome-extension/tests/*.test.js

# 扩展端到端（需要 GUI，会弹出受控 Chrome 窗口；自带 mock，无需 daemon/网络）
node chrome-extension/e2e/extension-e2e.test.js
```

## 提交规范（必须遵守）

所有提交使用 **Conventional Commits**：

```
type(scope): subject

body（可选，说明「为什么」）

BREAKING CHANGE: ...（仅破坏性变更）
```

- `type`：`feat` / `fix` / `docs` / `refactor` / `perf` / `test` / `chore` / `build` / `ci`
- `scope`：改动主要在 `chrome-extension/` 时用 `extension`；其他常用
  `server` / `worker` / `queue` / `bili-api` / `cli` / `docs`
- `subject`：祈使句，不超过 50 字符，结尾不加句号
- 破坏性变更必须以 `BREAKING CHANGE:` 开头单独成段

示例：

```
feat(extension): 遮挡层支持颜色与透明度配置
fix(worker): -799 限速时按 60/300/600s 退避并联动 bucket 冷却
```

### 提交前必须完成

1. 跑相关测试（见上），确保不破坏现有用例
2. **更新 `CHANGELOG.md` 的 `[Unreleased]` 段**：按用户可见变更组织
   （新增/修复/变更各列一条，写清楚功能点，中文）
3. `git add` 相关文件，**不要** add `venv/`、`__pycache__/`、
   `chrome-extension/dist/`、`*.pem`、cookie 等
4. 用规范格式提交；**如果用户没有给 message，由你根据 diff 生成**，
   CHANGELOG 的更新与对应代码放进同一个 commit

### 禁止

- 提交真实 cookie / API 密钥 / 私钥（`.pem`）
- 把 `venv/`、构建产物、下载的音频视频文件纳入提交
- 用 `-m` 传多行但不符合格式；绕过 `commit-msg` hook 的校验

## 本地 git 钩子

钩子文件在 `.githooks/`，启用（一次即可，写入仓库本地配置）：

```bash
git config core.hooksPath .githooks
```

- `.githooks/commit-msg`：校验提交信息格式，不符合直接拒绝
- `.githooks/prepare-commit-msg`：可选——设置环境变量 `B2TEXT_LLM_COMMIT=1`
  时，`git commit` 会自动调用 `scripts/gen-commit-message.sh` 用 LLM
  生成草稿（需要 `LLM_API_KEY` 等环境变量，见脚本头注释）

## 常见入口

- 起 daemon：`./venv/bin/b2text serve start`
- 本地直跑：`./venv/bin/b2text run <BV号|本地文件> -o <输出>`
- 打包扩展：`./chrome-extension/package.sh [--crx]`
