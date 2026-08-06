#!/usr/bin/env bash
# 根据暂存区 diff 生成 Conventional Commits 提交信息（OpenAI 兼容接口）。
#
# 环境变量：
#   LLM_BASE_URL  默认 https://api.openai.com/v1（可用 vLLM/Ollama/本地网关）
#   LLM_API_KEY   必填
#   LLM_MODEL     默认 gpt-4o-mini
#
# 用法：
#   git add . && ./scripts/gen-commit-message.sh
#   B2TEXT_LLM_COMMIT=1 git commit     # 或交给 prepare-commit-msg 钩子
set -euo pipefail

BASE_URL="${LLM_BASE_URL:-https://api.openai.com/v1}"
MODEL="${LLM_MODEL:-gpt-4o-mini}"
: "${LLM_API_KEY:?请先设置 LLM_API_KEY}"

DIFF="$(git diff --cached)"
if [ -z "$DIFF" ]; then
  echo "没有暂存内容，先 git add" >&2
  exit 1
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

cat > "$TMP" <<'EOF'
你是 b2text 仓库的提交信息助手。根据下面提供的 git diff 生成一条 Conventional Commits 提交信息：
- 格式：type(scope): subject；type 取 feat|fix|docs|refactor|perf|test|chore|build|ci；
  scope 改动在 chrome-extension/ 时用 extension，其余常用 server|worker|queue|bili-api|cli|docs
- subject 用祈使句、不超过 50 字符、结尾不加句号
- 有破坏性变更时，另起一段写 BREAKING CHANGE: 说明
- 只输出提交信息本身（subject + 可选 body），不要输出解释或其他内容

diff:
EOF
printf '%s' "$DIFF" >> "$TMP"

python3 - "$BASE_URL" "$MODEL" "$LLM_API_KEY" "$TMP" <<'PY'
import json
import sys
import urllib.request

base_url, model, api_key, prompt_file = sys.argv[1:5]
with open(prompt_file, encoding="utf-8") as f:
    prompt = f.read()

payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": "你是提交信息助手，只输出 Conventional Commits 格式。"},
        {"role": "user", "content": prompt},
    ],
    "temperature": 0.2,
}
req = urllib.request.Request(
    base_url.rstrip("/") + "/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    },
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    message = (data["choices"][0]["message"]["content"] or "").strip()
    print(message)
except Exception as exc:  # noqa: BLE001
    print(f"调用 LLM 失败：{exc}", file=sys.stderr)
    sys.exit(1)
PY
