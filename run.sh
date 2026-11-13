#!/usr/bin/env bash
# 段永平雪球推送：一键运行。
# 令牌放 local.env（已被 .gitignore 忽略），格式：
#   XQ_NTFY_TOKEN=tk_xxxxxxxx
set -euo pipefail
cd "$(dirname "$0")"
if [ -f local.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./local.env
  set +a
fi
exec python3 xq_push.py "$@"
