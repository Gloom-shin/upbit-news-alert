#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# 0) 사전 점검
command -v gh >/dev/null || { echo "[ERR] gh CLI 필요. brew install gh"; exit 1; }
if ! gh auth status >/dev/null 2>&1; then
  echo "[WARN] gh 인증 미확인 — deploy.command에서 처리됐다면 무시. 그대로 진행합니다."
fi
[ -f ~/.ssh/coin-trader-key.pem ] || { echo "[ERR] ~/.ssh/coin-trader-key.pem 없음"; exit 1; }
[ -f .env ] || { echo "[ERR] .env 없음"; exit 1; }

REPO_OWNER="${REPO_OWNER:-Gloom-shin}"
REPO_NAME="upbit-news-alert"
EC2_HOST="52.78.139.118"
EC2_USER="ec2-user"
SSH_KEY="$HOME/.ssh/coin-trader-key.pem"

# 1) 샌드박스 잔재 청소 + git init
rm -rf .git .pytest_cache app/__pycache__ tests/__pycache__ 2>/dev/null || true
git init -b main
git add -A
git commit -m "Initial commit: upbit-news-alert v0.1"

# 2) GitHub 레포 생성 (이미 있으면 push만)
if gh repo view "$REPO_OWNER/$REPO_NAME" >/dev/null 2>&1; then
  echo "[OK] 레포 이미 존재 — origin만 추가해서 push"
  git remote add origin "https://github.com/$REPO_OWNER/$REPO_NAME.git" 2>/dev/null || true
  git push -u origin main
else
  gh repo create "$REPO_OWNER/$REPO_NAME" --private --source=. --remote=origin --push
fi

# 3) Secrets 등록
gh secret set EC2_HOST -b "$EC2_HOST" --repo "$REPO_OWNER/$REPO_NAME"
gh secret set EC2_USER -b "$EC2_USER" --repo "$REPO_OWNER/$REPO_NAME"
gh secret set EC2_SSH_KEY < "$SSH_KEY" --repo "$REPO_OWNER/$REPO_NAME"

# 4) EC2에 .env 미리 올림
scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new .env "$EC2_USER@$EC2_HOST:/tmp/.env-upbit-news-alert"
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  "mkdir -p ~/upbit-news-alert && mv /tmp/.env-upbit-news-alert ~/upbit-news-alert/.env && chmod 600 ~/upbit-news-alert/.env"

# 5) 배포 트리거
gh workflow run deploy.yml --repo "$REPO_OWNER/$REPO_NAME" || true
sleep 5
gh run watch --exit-status --repo "$REPO_OWNER/$REPO_NAME" || {
  echo "[WARN] Actions 실패 또는 타임아웃 — 로그 확인:"
  gh run list --limit 3 --repo "$REPO_OWNER/$REPO_NAME"
  exit 1
}

# 6) EC2 검증
echo "=== docker compose ps ==="
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  "cd ~/upbit-news-alert && docker compose ps"
echo "=== 부팅 로그 (최근 30줄) ==="
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  "cd ~/upbit-news-alert && docker compose logs --tail 30 app"
echo "=== 가격 모니터링 1사이클 ==="
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  "cd ~/upbit-news-alert && docker compose run --rm app python -m app.main --once price"

echo ""
echo "✅ 배포 완료. vkdghckdh@gmail.com 으로 1~2시간 내 S/A급 알림 수신 가능."
