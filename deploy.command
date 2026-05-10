#!/usr/bin/env bash
# 더블클릭으로 실행되는 자동 배포 스크립트
set -uo pipefail

cd "$(dirname "$0")"

echo "════════════════════════════════════════════"
echo "  upbit-news-alert 자동 배포"
echo "════════════════════════════════════════════"
echo ""

# 0a) Homebrew 확인
if ! command -v brew >/dev/null 2>&1; then
  echo "[ERR] Homebrew가 필요합니다. 다음 명령을 먼저 실행해주세요:"
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo ""
  echo "Press any key to close..."; read -n 1
  exit 1
fi

# 0b) gh CLI 자동 설치
if ! command -v gh >/dev/null 2>&1; then
  echo "[INFO] gh CLI 설치 중..."
  brew install gh || { echo "[ERR] gh 설치 실패"; read -n 1; exit 1; }
fi

# 0c) gh 인증 확인 — 안 되어있으면 인터랙티브 로그인
if ! gh auth status >/dev/null 2>&1; then
  echo "[INFO] GitHub 인증이 필요합니다. 브라우저가 열립니다."
  echo "       [Login with a web browser] 선택 → 6자리 코드 복사 → 브라우저에 붙여넣기"
  echo ""
  gh auth login --hostname github.com --git-protocol https --web || {
    echo "[ERR] GitHub 로그인 실패"; read -n 1; exit 1;
  }
fi

# 0d) SSH 키 확인
if [ ! -f "$HOME/.ssh/coin-trader-key.pem" ]; then
  echo "[ERR] ~/.ssh/coin-trader-key.pem 없음. coin-trader 프로젝트에서 사용한 SSH 키가 필요합니다."
  read -n 1; exit 1
fi

# 0e) .env 확인
if [ ! -f .env ]; then
  echo "[ERR] .env 없음 (이 폴더에 .env가 있어야 합니다)"
  read -n 1; exit 1
fi

# 1) deploy.sh 실행
if [ ! -x ./deploy.sh ]; then
  chmod +x ./deploy.sh
fi

bash ./deploy.sh
RC=$?

echo ""
if [ "$RC" -eq 0 ]; then
  echo "✅ 배포 성공! vkdghckdh@gmail.com 으로 1~2시간 내 첫 알림 도착 예정"
else
  echo "❌ 배포 실패 — 위 로그 확인. 종료 코드: $RC"
fi
echo ""
echo "이 창은 닫으셔도 됩니다. 아무 키나 누르면 종료..."
read -n 1
