# Patent PDF Analyzer Backend

특허 공보 PDF를 수신하여 텍스트를 파싱하고, 5개 AI 모델을 순차 실행한 뒤 분석 보고서를 JSON으로 반환하는 백엔드 서버.

## 기술 스택

| 구분 | 기술 |
|------|------|
| 웹 프레임워크 | FastAPI |
| 태스크 큐 | Celery 5.x + Redis 7.x |
| PDF 파싱 | RunPod Serverless |
| 로깅 | loguru (JSON 파일 + 콘솔) |
| 컨테이너 | Docker + docker-compose |

## 아키텍처

```
메인 서버 ──POST /analyze──▶ FastAPI ──큐잉──▶ Redis
                                                │
                                          Celery Worker
                                           ├─ RunPod (PDF 파싱)
                                           ├─ Model 1~5 (순차 실행)
                                           └─ 보고서 포맷팅
                                                │
메인 서버 ◀──GET /result/{task_id}──── FastAPI ◀─┘
```

처리 시간이 15초 이상 소요되므로 **비동기 Polling 패턴**을 사용합니다.  
`POST /analyze` → 202 + `task_id` 즉시 반환 → `GET /result/{task_id}`로 결과 폴링.

## 빠른 시작

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일에서 RUNPOD_API_URL, RUNPOD_API_KEY 입력

# 2. 실행 (API + Worker + Redis)
docker compose up -d

# 3. 확인
curl http://localhost:8000/health
```

Swagger 문서: http://localhost:8000/docs

로컬은 기본 `docker-compose.yml` 기준으로 `8000` 포트를 사용합니다.

## EC2 최초 세팅 (Ubuntu 22.04)

아래 순서대로 실행하면 EC2에서 Docker 기반 초기 세팅부터 배포까지 진행할 수 있습니다.

```bash
# 1) EC2 접속 후 프로젝트 클론
git clone <repo-url> jd-backend
cd jd-backend

# 2) 초기 세팅 스크립트 실행 (Docker + Docker Compose)
chmod +x scripts/setup-ec2.sh
./scripts/setup-ec2.sh
```

`setup-ec2.sh` 실행 후에는 `docker` 그룹 권한 반영을 위해 **반드시 재접속**하세요.

```bash
# 3) SSH 재접속 후 버전 확인
docker --version
docker compose version

# 4) 환경변수 설정
cp .env.example .env
vi .env

# 5) 배포 스크립트 실행
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

### 참고

- 배포 시 `deploy.sh`는 `docker-compose.yml + docker-compose.prod.yml`를 함께 사용해 API를 `80` 포트로 노출합니다.
- API 확인: `http://<EC2_PUBLIC_IP>/health`
- Swagger: `http://<EC2_PUBLIC_IP>/docs`
- Flower(선택): `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile monitoring up -d` 후 `http://<EC2_PUBLIC_IP>:5555`
- EC2 보안그룹에서 최소한 `22`, `80` 포트만 허용하고, `6379`(Redis)는 외부에 열지 않는 것을 권장합니다.

## 실행 명령어

```bash
# 로컬 실행 (포트 8000)
docker compose up -d

# 배포 실행 (포트 80)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Flower 모니터링 포함
docker compose --profile monitoring up -d

# Worker 스케일 아웃 (예: 3개)
docker compose up -d --scale worker=3

# 로그 확인
docker compose logs -f worker
tail -f logs/error.log
```

## 환경변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `REDIS_URL` | Redis 접속 URL | `redis://redis:6379/0` |
| `RUNPOD_API_URL` | RunPod Serverless 엔드포인트 URL | - |
| `RUNPOD_API_KEY` | RunPod API 키 | - |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |

---

## API 명세

### `GET /health`

서버 상태 확인.

**Response** `200`
```json
{ "status": "ok" }
```

---

### `POST /api/v1/analyze`

특허 PDF 파일을 업로드하여 분석을 시작한다.

**Request**
- Content-Type: `multipart/form-data`
- Body: `file` (PDF 파일, 최대 100MB)

**Response** `202 Accepted`
```json
{
  "task_id": "a1b2c3d4-e5f6-...",
  "status": "queued",
  "message": "분석 요청이 접수되었습니다. GET /api/v1/result/{task_id}로 결과를 확인하세요."
}
```

**Error Responses**

| 코드 | 조건 |
|------|------|
| `400` | PDF가 아닌 파일, 빈 파일, 파일명 누락 |
| `413` | 파일 크기 100MB 초과 |

---

### `GET /api/v1/result/{task_id}`

분석 결과를 조회한다. 처리 완료 전까지 반복 폴링.

**Path Parameter**
- `task_id` (string): `POST /analyze` 에서 반환받은 태스크 ID

**Response** — 상태에 따라 형태가 달라짐:

#### 대기 중
```json
{ "task_id": "...", "status": "queued" }
```

#### 처리 중 (커스텀 상태)
```json
{ "task_id": "...", "status": "MODEL_2", "detail": "Model 2/5 실행 중" }
```

가능한 `status` 값: `PARSING`, `MODEL_1`, `MODEL_2`, `MODEL_3`, `MODEL_4`, `MODEL_5`, `FORMATTING`

#### 완료
```json
{
  "task_id": "...",
  "status": "completed",
  "result": {
    "report": {
      "generated_at": "2026-02-10T12:00:00+00:00",
      "version": "0.1.0",
      "sections": {
        "bibliographic": { ... },
        "claims_analysis": { ... },
        "technical_field": { ... },
        "figures_and_embodiments": { ... },
        "overall_evaluation": { ... }
      }
    }
  }
}
```

#### 실패
```json
{ "task_id": "...", "status": "failed", "error": "에러 메시지" }
```

---

## 프로젝트 구조

```
jd-backend/
├── app/
│   ├── main.py              # FastAPI 앱 + 미들웨어 + 예외 핸들러
│   ├── config.py             # 환경변수 설정
│   ├── logging_config.py     # loguru 로깅 설정
│   ├── api/
│   │   └── routes.py         # API 엔드포인트
│   ├── services/
│   │   ├── pdf_service.py    # RunPod PDF 파싱
│   │   └── report_service.py # JSON 보고서 포맷팅
│   ├── models/
│   │   └── model_1~5.py      # AI 모델 (스텁)
│   └── worker/
│       ├── celery_app.py     # Celery 설정
│       └── tasks.py          # Task 파이프라인
├── scripts/
│   ├── setup-ec2.sh          # EC2 초기 세팅
│   └── deploy.sh             # 배포 스크립트
├── Dockerfile                # 멀티스테이지 빌드
├── docker-compose.yml
└── .env.example
```

## 로깅

모든 로그에 `request_id`가 포함되어 API 요청부터 Celery Task까지 추적 가능.

| 파일 | 내용 | 포맷 |
|------|------|------|
| 콘솔 (stderr) | 전체 로그 | 컬러 텍스트 |
| `logs/app.log` | 전체 로그 | JSON (50MB rotation, 7일 보관) |
| `logs/error.log` | ERROR 이상 | JSON (10MB rotation, 30일 보관) |
