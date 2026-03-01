#!/bin/bash
# 监视 bookmarkIntegrated/edge.html 与 Via/bookmarks.html，有写入后触发一次同步（带冷却）；
# 另加定时回退（每 FALLBACK_SEC 秒），防止 Via 客户端上传未触发 inotify 导致删除未同步到 Edge。
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
INTEGRATED="${BOOKMARK_INTEGRATED_DIR:-/data/webdav/bookmark/bookmarkIntegrated}"
EDGE="${INTEGRATED}/edge.html"
VIA="${INTEGRATED}/Via/bookmarks.html"
COOLDOWN_SEC=60
FALLBACK_SEC=90
# 优先 venv，无则用系统 python3（适配未建 venv 的机器）
if [[ -x "${DIR}/.venv/bin/python" ]]; then
  PYTHON="${DIR}/.venv/bin/python"
else
  PYTHON=python3
fi
SYNC="${DIR}/sync.py"

LAST_RUN_FILE="${INTEGRATED}/.last_sync_trigger"

run_sync() {
    echo "[$(date -Iseconds)] run sync (triggered)"
    "$PYTHON" "$SYNC" || true
    date +%s > "$LAST_RUN_FILE"
}

maybe_sync() {
    local now last=0
    now=$(date +%s)
    [[ -f "$LAST_RUN_FILE" ]] && last=$(cat "$LAST_RUN_FILE")
    if (( now - last >= COOLDOWN_SEC )); then
        run_sync
    fi
}

while true; do
    mkdir -p "$(dirname "$VIA")"
    [[ -f "$VIA" ]] || touch "$VIA"
    [[ -f "$EDGE" ]] || touch "$EDGE"

    # inotify 最多等 FALLBACK_SEC 秒；超时（exit 2）时管道最后是 while，出口码恒为 0，故用 PIPESTATUS 判断
    inotifywait -q -e close_write -t "$FALLBACK_SEC" --format '%w%f' "$EDGE" "$VIA" 2>/dev/null | while read -r _; do
        maybe_sync
    done
    [[ ${PIPESTATUS[0]} -ne 0 ]] && maybe_sync
    sleep 2
done
