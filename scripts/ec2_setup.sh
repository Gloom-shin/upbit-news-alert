#!/bin/bash
# EC2 Amazon Linux 2023 초기 세팅 스크립트
# 실행: bash ec2_setup.sh

set -e

echo "=== 1. 시스템 업데이트 ==="
sudo yum update -y

echo "=== 2. Docker 설치 ==="
sudo yum install -y docker git
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

echo "=== 3. Docker Compose 설치 ==="
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
sudo ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose

echo "=== 4. 프로젝트 클론 ==="
cd /home/ec2-user
git clone https://github.com/YOUR_USERNAME/coin-trader.git
cd coin-trader

echo "=== 5. .env 파일 생성 ==="
cat > .env << 'EOF'
DRY_RUN=true
UPBIT_ACCESS_KEY=your_key
UPBIT_SECRET_KEY=your_secret
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
EMAIL_SENDER=
EMAIL_APP_PASSWORD=
EMAIL_RECIPIENT=
ANTHROPIC_API_KEY=
STRATEGY_INTERVAL_MINUTES=30
EMAIL_HOUR=8
EOF

echo ""
echo "⚠️  .env 파일을 실제 값으로 수정하세요:"
echo "   nano /home/ec2-user/coin-trader/.env"
echo ""

echo "=== 6. 서버 시작 ==="
# docker 그룹 적용을 위해 새 셸에서 실행
newgrp docker << 'DOCKEREOF'
cd /home/ec2-user/coin-trader
docker compose up -d --build
DOCKEREOF

echo ""
echo "✅ 완료! 상태 확인:"
echo "   docker compose ps"
echo "   curl http://localhost:8000/api/health"
