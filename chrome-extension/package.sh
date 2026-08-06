#!/usr/bin/env bash
# 打包 b2text 扩展。
#
# 用法：
#   ./package.sh            # 生成 dist/b2text-extension.zip（Web Store 格式）
#   ./package.sh --crx      # 额外用 Chrome CLI 生成 dist/b2text-extension.crx
#
# 注意：新版 Chrome 已移除 chrome://extensions 里的 "Pack extension" 按钮，
# 命令行 --pack-extension 仍可用；若本机不可用则只产出 zip。
set -euo pipefail

cd "$(dirname "$0")"

NAME="b2text-extension"
OUT="dist"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 打进包的文件（manifest 引用的全部资源）
FILES=(manifest.json background.js content.js content.css lib options popup)

rm -rf "$OUT"
mkdir -p "$OUT"

echo "==> 生成 $OUT/$NAME.zip"
zip -r "$OUT/$NAME.zip" "${FILES[@]}" -x "*.DS_Store" >/dev/null
echo "    ✅ $OUT/$NAME.zip"

if [[ "${1:-}" == "--crx" ]]; then
  if [ ! -x "$CHROME" ]; then
    echo "    ⚠️  未找到 Chrome（$CHROME），跳过 crx"
    exit 0
  fi
  echo "==> 用 Chrome 打包 crx（首次会生成私钥 $NAME.pem，请妥善保存）"
  PACK_DIR="$OUT/src"
  rm -rf "$PACK_DIR"
  mkdir -p "$PACK_DIR"
  cp -R "${FILES[@]}" "$PACK_DIR/"
  "$CHROME" --pack-extension="$PWD/$PACK_DIR" >/dev/null 2>&1 || {
    echo "    ⚠️  --pack-extension 失败（新版 Chrome 可能已移除），跳过 crx"
    exit 0
  }
  mv "$OUT/chrome-extension.crx" "$OUT/$NAME.crx" 2>/dev/null || true
  mv "$OUT/chrome-extension.pem" "$OUT/$NAME.pem" 2>/dev/null || true
  rm -rf "$PACK_DIR"
  echo "    ✅ $OUT/$NAME.crx（配套私钥 $OUT/$NAME.pem）"
fi

echo "==> 完成，产物在 $(cd "$OUT" && pwd)"
