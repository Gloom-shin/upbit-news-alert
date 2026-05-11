#!/usr/bin/env bash
# 로컬 변경 내용을 GitHub에 push
set -uo pipefail
cd "$(dirname "$0")"

echo "════════════════════════════════════════════"
echo "  upbit-news-alert 변경사항 push"
echo "════════════════════════════════════════════"
echo ""

if [ ! -d .git ]; then
  echo "[ERR] .git 없음. deploy.command 먼저 실행하세요."
  read -n 1; exit 1
fi

# 0) 잔존 락 파일 제거 (이전 작업이 비정상 종료되었을 때)
rm -f .git/index.lock 2>/dev/null || true

# 1) 원격 최신 상태 가져오기 (로컬과 분기되어 있을 수 있음)
echo "[1/4] 원격 fetch..."
git fetch origin main || { echo "[ERR] fetch 실패. 인터넷/인증 확인"; read -n 1; exit 1; }

# 2) 로컬 변경사항을 원격 HEAD 위에 얹어 정렬
#    deploy.command가 .git을 새로 만든 경우 로컬이 1커밋, 원격이 N커밋으로 어긋날 수 있음.
#    원격을 base로 삼아 워킹 트리 변경사항을 그 위에 commit.
echo "[2/4] 원격 main 기준으로 로컬 정렬..."
git reset --soft origin/main 2>/dev/null || true

# 3) 변경 요약
echo ""
echo "변경된 파일:"
git status --short
echo ""

git add -A
if git diff --cached --quiet; then
  echo "[INFO] commit할 변경 없음 — 원격과 동일"
  read -n 1; exit 0
fi

echo "[3/4] commit..."
git -c user.email="vkdghckdh@gmail.com" -c user.name="Gloom-shin" \
  commit -m "feat: 매칭 정확도 개선 + 주간 알림 효과 검증 리포트

- news.py: 업비트 마켓 메타에서 한글/영문명 동적 매핑 (오탐 줄임)
- news.py: 모호한 키워드(코스모스, 톤, 스텔라 등) 차단 리스트
- classifier.py: relevant 필드 추가, false면 강제 C 다운그레이드
- outcome_report.py: 주간 등급별/기준별 성과 통계 + Claude 인사이트
- workflows/outcome-report.yml: 매주 일요일 23:00 KST 발송"

echo "[4/4] push..."
git push origin main
RC=$?

echo ""
if [ $RC -eq 0 ]; then
  echo "✅ Push 완료. 다음 Monitor 실행(30분 이내)부터 새 로직 적용"
  echo ""
  echo "Monitor 실행 보기: https://github.com/Gloom-shin/upbit-news-alert/actions/workflows/monitor.yml"
else
  echo "❌ Push 실패. 위 에러 확인"
fi

echo ""
echo "아무 키나 누르면 종료..."
read -n 1
