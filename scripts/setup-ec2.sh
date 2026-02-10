#!/usr/bin/env bash
# =============================================================================
# EC2 초기 세팅 스크립트 (Amazon Linux 2023 / Ubuntu 22.04)
#
# EC2 인스턴스에 SSH 접속 후 최초 1회 실행:
#   chmod +x scripts/setup-ec2.sh
#   ./scripts/setup-ec2.sh
# =============================================================================

set -euo pipefail

echo "=== EC2 초기 세팅 시작 ==="

# Docker 설치 확인
if ! command -v docker &> /dev/null; then
    echo ">>> Docker 설치 중..."
    # Amazon Linux 2023
    if [ -f /etc/system-release ]; then
        sudo yum update -y
        sudo yum install -y docker
        sudo systemctl start docker
        sudo systemctl enable docker
        sudo usermod -aG docker $USER
    # Ubuntu
    elif [ -f /etc/lsb-release ]; then
        sudo apt-get update
        sudo apt-get install -y docker.io
        sudo systemctl start docker
        sudo systemctl enable docker
        sudo usermod -aG docker $USER
    fi
    echo "Docker 설치 완료. 그룹 변경 적용을 위해 재로그인 필요."
else
    echo "Docker 이미 설치됨: $(docker --version)"
fi

# Docker Compose 설치 확인 (V2 plugin)
if ! docker compose version &> /dev/null; then
    echo ">>> Docker Compose 플러그인 설치 중..."
    sudo mkdir -p /usr/local/lib/docker/cli-plugins
    sudo curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    echo "Docker Compose 설치 완료: $(docker compose version)"
else
    echo "Docker Compose 이미 설치됨: $(docker compose version)"
fi

echo ""
echo "=== 초기 세팅 완료 ==="
echo "다음 단계:"
echo "  1. 재로그인 (docker 그룹 적용): exit 후 다시 SSH 접속"
echo "  2. 프로젝트 클론: git clone <repo-url> jd-backend"
echo "  3. 환경변수 설정: cd jd-backend && cp .env.example .env && vi .env"
echo "  4. 배포: ./scripts/deploy.sh"
