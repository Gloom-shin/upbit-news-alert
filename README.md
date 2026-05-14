# upbit-news-alert

업비트 KRW 마켓에서 **2~3일 연속 일봉 양봉**인 종목을 추리고, 코인니스/토큰포스트 RSS를 5분마다
훑어 그 종목의 호재 뉴스를 발견하면 Claude Haiku로 **S/A/B/C 등급** 판정 후 **S·A급만 이메일 알림**.
이후 매일 자정 KST에 4가지 종료 기준(첫 음봉 / 트레일링 -7% / 본전 이탈 / 연속 2일 음봉)을 추적해
SQLite에 기록하고, `python -m app.report` 로 등급별 평균 상승 지속일을 본다.

## 가장 빠른 배포 (권장) — `redeploy-local.command`

**⚠️ GitHub Actions 배포(`deploy.command`)는 EC2 보안그룹이 GitHub 러너 IP를 차단해
`appleboy/ssh-action`에서 `dial tcp ***:22: i/o timeout`이 발생합니다. 사용자 Mac에서
EC2로 직접 SSH 푸시하는 `redeploy-local.command`를 사용하세요. (필요시 보안그룹에 GitHub
Actions IP 대역을 추가하면 Actions 배포도 가능하지만, 매주 IP가 바뀌므로 비추천)**

Finder에서 `~/Downloads/upbit-news-alert/` 폴더 열고 **`redeploy-local.command`** 파일을 **더블클릭**하세요.

- Terminal이 자동으로 열리고 EC2에 직접 SSH로 `.env` 업로드 → `git pull` → `docker build` → `docker compose up -d` 까지 수행합니다
- `gh` 인증이나 GitHub 레포 생성 같은 단계가 필요 없습니다 (이미 푸시된 레포에서 clone)
- 처음 실행 시 macOS가 "확인되지 않은 개발자" 경고를 띄우면: 시스템 설정 → 개인정보 보호 및 보안 → "그래도 열기" 클릭
- 또는 마우스 우클릭(또는 Control+클릭) → "열기" 선택

> `.github/workflows/deploy.yml`은 보안그룹 문제로 매 push마다 실패 알림이 옵니다.
> 알림이 거슬리면 GitHub 레포 → Actions 탭 → "Deploy to AWS EC2" → ⋯ → Disable workflow 로 비활성화하세요.

## 최초 배포 (이미 끝난 경우 무시) — `deploy.command`

Finder에서 `~/Downloads/upbit-news-alert/` 폴더 열고 **`deploy.command`** 파일을 **더블클릭**하세요.

- Terminal이 자동으로 열리고 모든 단계가 자동 실행됩니다
- gh CLI가 없으면 자동 설치, GitHub 인증이 안 되어있으면 브라우저가 자동으로 열립니다
- 처음 실행 시 macOS가 "확인되지 않은 개발자" 경고를 띄우면: 시스템 설정 → 개인정보 보호 및 보안 → "그래도 열기" 클릭
- 또는 마우스 우클릭(또는 Control+클릭) → "열기" 선택

## 한 줄 배포: `bash deploy.sh`

`gh` CLI 로그인 + `~/.ssh/coin-trader-key.pem` + `.env` 만 갖춰져 있으면 끝. 스크립트가
git init → GitHub private 레포 생성 → Secrets 등록 → EC2에 `.env` 업로드 → Actions 트리거 →
`docker compose ps` + 부팅 로그 + `--once price` 1사이클 검증까지 한 번에 처리한다. 자세한 단계는
`deploy.sh` 본문 참조.

## 빠른 시작 (로컬)

```bash
cd ~/Downloads/upbit-news-alert
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 그리고 키 채우기

# 동작 검증 (네트워크만 필요)
python -m app.main --dry-run
python -m app.main --once price

# 본 실행
python -m app.main
```

### 단위 테스트

```bash
pip install pytest
PYTHONPATH=. pytest tests/ -v
```

## 환경 변수

| 키 | 필수 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude API 키 (sk-ant-...) |
| `GMAIL_USER` | ✅ | 발신 Gmail 주소 |
| `GMAIL_APP_PASSWORD` | ✅ | Gmail App Password (2단계인증 후 발급) |
| `EMAIL_RECIPIENT` | ✅ | 수신자 이메일 |
| `MIN_CONSECUTIVE_UP_DAYS` | | 기본 2 |
| `MAX_CONSECUTIVE_UP_DAYS` | | 기본 3 |
| `PRICE_INTERVAL_MIN` | | 기본 60 |
| `NEWS_INTERVAL_MIN` | | 기본 5 |
| `TRACK_HOUR_KST` / `TRACK_MINUTE_KST` | | 기본 00:05 |
| `TRAILING_DROP_THRESHOLD` | | 기본 0.07 (-7%) |
| `CONSECUTIVE_RED_DAYS` | | 기본 2 |
| `DRY_RUN` | | true 시 이메일 대신 로그만 |
| `LOG_LEVEL` | | INFO/DEBUG/WARNING |
| `TZ` | | Asia/Seoul |
| `COINNESS_RSS` | | 기본 https://coinness.live/rss |
| `TOKENPOST_RSS` | | 기본 https://www.tokenpost.kr/rss |

## CLI

| 명령 | 설명 |
|---|---|
| `python -m app.main` | 스케줄러 실행 (블로킹) |
| `python -m app.main --dry-run` | 환경 검증 + 모듈 import 확인 |
| `python -m app.main --once price` | 가격 모니터링 1사이클 |
| `python -m app.main --once news` | 뉴스 수집 + 알림 1사이클 |
| `python -m app.main --once track` | 활성 트래킹 4기준 평가 1사이클 |
| `python -m app.main --once drain` | pending 큐 일괄 발송 1사이클 |
| `python -m app.report` | 등급별 평균 상승일 통계 |

## DB 스키마

`data/alerts.db` (SQLite). 3개 테이블:

- **news_events** — 매칭된 뉴스 1건 = 1행 (URL UNIQUE, 등급/요약/감지가격)
- **price_tracking** — 알림 발송된 종목별 진행 상태 (entry / peak / closed_at / close_reason)
- **event_outcomes** — 한 트래킹에 대한 4기준 발생 기록 (criterion UNIQUE per tracking)

## EC2 배포 (coin-trader 인프라 재사용)

GitHub Actions → SSH → EC2(`52.78.139.118`) → docker compose up. coin-trader와 같은 인스턴스에
별도 컨테이너로 동작 (포트 노출 없음, 알림 봇이라 인바운드 불필요).

### 최초 1회 셋업 (EC2 측)

```bash
ssh -i ~/.ssh/coin-trader-key.pem ec2-user@52.78.139.118

# .env 작성 (로컬 .env 그대로 복사)
mkdir -p ~/upbit-news-alert
cd ~/upbit-news-alert
cat > .env <<'EOF'
ANTHROPIC_API_KEY=...
GMAIL_USER=...
GMAIL_APP_PASSWORD=...
EMAIL_RECIPIENT=...
LOG_LEVEL=INFO
TZ=Asia/Seoul
EOF
chmod 600 .env
```

### CI/CD (한 번만)

`coin-trader` 레포에 등록한 Secrets(`EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY`)와 동일한 값을
`upbit-news-alert` 레포에도 등록. 이후 `main` push마다 자동 배포.

```bash
gh secret set EC2_HOST -b "52.78.139.118" --repo Gloom-shin/upbit-news-alert
gh secret set EC2_USER -b "ec2-user"      --repo Gloom-shin/upbit-news-alert
gh secret set EC2_SSH_KEY < ~/.ssh/coin-trader-key.pem --repo Gloom-shin/upbit-news-alert
```

### 운영 명령

```bash
ssh -i ~/.ssh/coin-trader-key.pem ec2-user@52.78.139.118 \
    "cd ~/upbit-news-alert && docker compose ps && docker compose logs --tail 50 app"
```

## 설계 메모

- **인증 불필요**: Upbit 공개 API만 사용 (잔고/주문 X) → 401 걱정 없음.
- **알림 도배 방지**: `news_events.url` UNIQUE → 같은 URL 재처리 안 함. 또한 등급 분류 후 S·A만 이메일.
- **종료 기준 4개 동시 기록**: 어떤 게 먼저 발동했나 추후 백테스트 가능. `primary_close_reason` 우선순위:
  같은 인덱스에 동시 발생 시 `first_red > below_entry > trailing_drop > consecutive_red` (보수적).
- **로컬 vs EC2 .env 분리**: 배포 시 EC2의 `.env`는 덮어쓰지 않음 (`git reset` 대상에서 제외 — `.gitignore`).
