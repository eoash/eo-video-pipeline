#!/bin/bash
# launchd 데몬 설치 — watch 모드 상시 실행
set -euo pipefail

PLIST_NAME="com.eostudio.video-pipeline.plist"
SRC="$(cd "$(dirname "$0")/.." && pwd)/${PLIST_NAME}"
DEST="$HOME/Library/LaunchAgents/${PLIST_NAME}"
LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 기존 데몬 중지 (있으면)
launchctl bootout "gui/$(id -u)/${PLIST_NAME}" 2>/dev/null || true

# plist 심볼릭 링크
ln -sf "$SRC" "$DEST"
echo "✅ Plist linked: $DEST"

# 로드
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "✅ 데몬 시작: $PLIST_NAME"
echo "   로그: $LOG_DIR/pipeline.out.log"
echo ""
echo "상태 확인: launchctl print gui/$(id -u)/${PLIST_NAME}"
echo "중지: launchctl bootout gui/$(id -u)/${PLIST_NAME}"
