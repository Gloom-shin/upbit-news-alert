#!/usr/bin/env bash
# GitHub Actions를 우회하고 EC2에 직접 SSH로 배포
set -euo pipefail

cd "$(dirname "$0")"

SSH_KEY="$HOME/.ssh/coin-trader-key.pem"
EC2_USER="ec2-user"
EC2_HOST="52.78.139.118"
REPO_URL="https://github.com/Gloom-shin/upbit-news-alert.git"
REMOTE_DIR="upbit-news-alert"

echo "════════════════════════════════════════════"
echo "  upbit-news-alert — 로컬에서 직접 EC2 배포"
echo "════════════════════════════════════════════"

# 사전 점검
[ -f "$SSH_KEY" ] || { echo "[ERR] $SSH_KEY 없음"; read -n 1; exit 1; }
[ -f .env ] || { echo "[ERR] 로컬 .env 없음"; read -n 1; exit 1; }

echo ""
echo "[1/6] EC2 연결 확인..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
  "$EC2_USER@$EC2_HOST" 'echo OK on $(hostname)' || {
  echo "[ERR] EC2 SSH 실패. 보안그룹에 본인 IP가 허용되어 있는지 확인."
  read -n 1; exit 1;
}

echo ""
echo "[2/6] docker compose 명령 감지 (plugin vs legacy)..."
DC_CMD=$(ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" bash <<'DETECT_EOF'
if docker compose version >/dev/null 2>&1; then
  echo "docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  echo "docker-compose"
else
  echo "NONE"
fi
DETECT_EOF
)
if [ "$DC_CMD" = "NONE" ]; then
  echo "[ERR] EC2에 docker compose / docker-compose 둘 다 없음."
  read -n 1; exit 1
fi
echo "  → '$DC_CMD' 사용"

echo ""
echo "[3/6] .env EC2에 업로드 (이미 있으면 덮어씀)..."
scp -i "$SSH_KEY" .env "$EC2_USER@$EC2_HOST:/tmp/.env-upload"
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  "mkdir -p ~/$REMOTE_DIR && mv /tmp/.env-upload ~/$REMOTE_DIR/.env && chmod 600 ~/$REMOTE_DIR/.env"

echo ""
echo "[4/6] 원격에서 코드 클론/업데이트..."
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  REPO_URL="$REPO_URL" REMOTE_DIR="$REMOTE_DIR" bash <<'REMOTE_EOF'
set -euo pipefail
cd ~
if [ ! -d "$REMOTE_DIR/.git" ]; then
  echo "  → 초기 clone"
  rm -rf "${REMOTE_DIR}.tmp"
  git clone "$REPO_URL" "${REMOTE_DIR}.tmp"
  # .env는 보존
  if [ -f "$REMOTE_DIR/.env" ]; then mv "$REMOTE_DIR/.env" "${REMOTE_DIR}.tmp/.env"; fi
  rm -rf "$REMOTE_DIR" && mv "${REMOTE_DIR}.tmp" "$REMOTE_DIR"
else
  echo "  → 기존 레포 업데이트"
  cd "$REMOTE_DIR"
  git fetch origin main
  git reset --hard origin/main
fi
REMOTE_EOF

echo ""
echo "[5/6] Docker 빌드 + 기동..."
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  REMOTE_DIR="$REMOTE_DIR" DC_CMD="$DC_CMD" bash <<'REMOTE_EOF'
set -euo pipefail
cd ~/"$REMOTE_DIR"
docker build -t upbit-news-alert .
$DC_CMD up -d
sleep 5
$DC_CMD ps
REMOTE_EOF

echo ""
echo "[6/6] 부팅 로그 확인..."
ssh -i "$SSH_KEY" "$EC2_USER@$EC2_HOST" \
  "cd ~/$REMOTE_DIR && $DC_CMD logs --tail 30 app"

echo ""
echo "✅ 배포 완료!"
echo "   - vkdghckdh@gmail.com 으로 1~2시간 내 첫 알림 도착 예정"
echo "   - 컨테이너 로그: ssh -i $SSH_KEY $EC2_USER@$EC2_HOST 'docker logs -f \$(docker ps -q -f name=upbit-news-alert)'"
echo ""
echo "이 창은 닫으셔도 됩니다. 아무 키나 누르면 종료..."
read -n 1
