#!/usr/bin/env bash
# Comfy-FlashVSR-Trunk 多平台同步脚本（Linux/macOS/Git Bash）
# 用法:
#   ./publish.sh            # 推送三个平台
#   ./publish.sh --deploy  # 推送 + 同步到本地 ComfyUI custom_nodes
set -euo pipefail

REPO="Comfy-FlashVSR-Trunk"
# remote 配置（分组/命名空间改动改这里）
ORIGIN="http://gitlab.merit-link.cn/rrmxyx/${REPO}.git"   # 内网 GitLab，分组 rrmxyx
GITHUB="git@github.com:yisino/${REPO}.git"
CODEUP="git@codeup.aliyun.com:5f28c467769820a3e817fc05/${REPO}.git"
COMFY_CUSTOM_NODES="${COMFY_CUSTOM_NODES:-$HOME/ComfyUI/custom_nodes}"

add_remote() {
  local name="$1" url="$2"
  if git remote get-url "$name" >/dev/null 2>&1; then
    git remote set-url "$name" "$url"
  else
    git remote add "$name" "$url"
  fi
  echo "remote[$name] -> $url"
}

add_remote origin  "$ORIGIN"
add_remote github  "$GITHUB"
add_remote codeup  "$CODEUP"

for r in origin github codeup; do
  echo ">> pushing to $r ..."
  git push "$r" --all
  git push "$r" --tags
done

if [[ "${1:-}" == "--deploy" ]]; then
  DEST="$COMFY_CUSTOM_NODES/$REPO"
  echo ">> deploying to $DEST"
  mkdir -p "$DEST"
  rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
        ./ "$DEST"/
  echo "deployed."
fi

echo "DONE."
