# ku-lms-downloader (`kulms`)

고려대학교 LMS(`mylms.korea.ac.kr`) **링크 하나만 붙여넣으면** 그 강의의
**첨부파일과 주차학습 영상**을 내려받는 가벼운 CLI 도구입니다.

- 의존성 없음 — **Python 표준 라이브러리만** 사용 (Python 3.10+)
- KSSO 단일 로그인(SSO) → LearningX 부트스트랩 → 모듈/과제/게시판 첨부 자동 해석
- 주차학습 영상(kucom/uniplayer)의 실제 다운로드 URL까지 해석해서 받습니다

> [`LMSintegration`](../LMSintegration) 프로젝트에서 Notion · Google Drive ·
> LLM 요약 · Whisper 전사 · 상태 DB를 모두 걷어내고, **"링크 → 다운로드"** 핵심만
> 추출한 경량 버전입니다.

---

## 설치

```bash
cd ku-lms-downloader
python -m venv .venv
. .venv/Scripts/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e .                # 'kulms' 명령 등록 (선택)
```

설치 없이 바로 실행해도 됩니다:

```bash
python -m kulms <링크>
```

## 로그인 정보 설정

`.env.example`을 `.env`로 복사하고 포털(KUPID) 계정을 채웁니다:

```ini
LMS_USERNAME=your_portal_id
LMS_PASSWORD=your_portal_password
```

환경변수로 넘겨도 됩니다. base URL은 보통 붙여넣은 링크에서 자동 추론되므로 비워둬도 됩니다.

## 사용법

> 아래 예시의 `kulms`는 `pip install -e .`로 명령을 등록했을 때 쓸 수 있는 단축형입니다.
> 설치하지 않았다면 `kulms`를 `python -m kulms`로 바꿔서 동일하게 실행하세요.
> (예: `python -m kulms https://mylms.korea.ac.kr/courses/12345`)

```bash
# 강의 홈 링크 → 주차학습 영상 + 강의자료 + 과제 첨부 모두
kulms https://mylms.korea.ac.kr/courses/12345

# 주차학습(모듈) 영상만
kulms https://mylms.korea.ac.kr/courses/12345/modules --videos-only

# 게시판까지 전부, 출력 폴더 지정
kulms 12345 --all -o ./내려받기

# 받지 않고 무엇이 있는지 목록만 보기
kulms https://mylms.korea.ac.kr/courses/12345 --list
```

붙여넣을 수 있는 링크 형태:

| 링크 | 받는 대상 |
| --- | --- |
| `.../courses/<id>` | 모듈(주차학습/강의자료) + 과제 |
| `.../courses/<id>/modules` | 모듈만 |
| `.../courses/<id>/assignments[/<n>]` | 과제(또는 특정 과제) |
| `<id>` (숫자만) | 모듈 + 과제 |

### 옵션

| 옵션 | 설명 |
| --- | --- |
| `-o, --out DIR` | 출력 폴더 (기본 `./downloads`) |
| `--modules` / `--assignments` / `--board` / `--all` | 받을 범위 지정 (미지정 시 링크에서 자동 판단) |
| `--videos-only` / `--files-only` | 영상만 / 영상 제외 |
| `--save-links` | 외부 링크를 `.url.txt`로 저장 |
| `--overwrite` | 이미 받은 파일도 다시 받기 (기본은 크기가 같으면 건너뜀) |
| `--list` | 다운로드 없이 목록만 출력 |
| `--base-url URL` | LMS base URL 강제 지정 |
| `-v, --verbose` | 자세한 로그 |

## 출력 구조

```
downloads/
└── <강의명>/
    ├── <1주차 제목>/
    │   ├── 강의영상.mp4
    │   └── 강의노트.pdf
    └── <과제 제목>/
        └── 첨부.zip
```

## 구조

```
kulms/
├── cli.py          # CLI 진입점 (인자 파싱 → 조회 → 다운로드)
├── urls.py         # 붙여넣은 링크 → (base_url, course_id, scope) 파싱
├── config.py       # .env / 환경변수 로딩
├── client.py       # KSSO 로그인 + LearningX/게시판 부트스트랩 + 첨부 해석 (표준 라이브러리)
├── downloader.py   # 인증 세션으로 파일 스트리밍 다운로드
└── models.py       # Course / Attachment / Material
```

## 테스트

```bash
pip install -e ".[dev]"
pytest
```

네트워크가 필요 없는 순수 로직(링크 파싱 · 미디어 판별 · kucom URL 조립 · 파일명 처리 · 범위 결정)을 검증합니다.

## 주의

- 본인 계정으로 **수강 중인 강의**의 자료만 받을 수 있습니다.
- 비밀번호는 KSSO 브리지의 RSA 공개키로 암호화되어 전송되며, 로컬에는 `.env`에만 저장됩니다(`.gitignore` 처리됨).
- 학교 시스템 점검·구조 변경 시 동작하지 않을 수 있습니다.
