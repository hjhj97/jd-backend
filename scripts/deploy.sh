#!/usr/bin/env bash
# =============================================================================
# EC2 배포 스크립트
#
# 사전 조건:
#   - EC2 인스턴스에 Docker + Docker Compose 설치
#   - .env 파일에 실제 환경변수 설정
#
# 사용법:
#   chmod +x scripts/deploy.sh
#   ./scripts/deploy.sh
# =============================================================================

set -euo pipefail

echo "=== jd-backend 배포 시작 ==="

COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

# .env 파일 확인
if [ ! -f .env ]; then
    echo "ERROR: .env 파일이 없습니다. .env.example을 복사하여 .env를 생성하세요."
    echo "  cp .env.example .env"
    exit 1
fi

# logs 디렉토리 생성
mkdir -p logs

# 이미지 빌드 + 컨테이너 시작
echo ">>> Docker 이미지 빌드 중..."
docker compose ${COMPOSE_FILES} build

echo ">>> 컨테이너 시작 중..."
docker compose ${COMPOSE_FILES} up -d

echo ">>> 컨테이너 상태 확인..."
docker compose ${COMPOSE_FILES} ps

echo ""
echo "=== 배포 완료 ==="
echo "  API:    http://<EC2_PUBLIC_IP>"
echo "  Docs:   http://<EC2_PUBLIC_IP>/docs"
echo "  Health: http://<EC2_PUBLIC_IP>/health"
echo ""
echo "모니터링(Flower)을 같이 띄우려면:"
echo "  docker compose ${COMPOSE_FILES} --profile monitoring up -d"
echo "  Flower: http://<EC2_PUBLIC_IP>:5555"
echo ""
echo "로그 확인:"
echo "  docker compose ${COMPOSE_FILES} logs -f worker"
echo "  tail -f logs/error.log"
