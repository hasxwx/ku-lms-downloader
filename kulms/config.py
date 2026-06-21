from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(slots=True)
class Config:
    username: str
    password: str
    login_user_id: str = ""
    base_url: str = ""

    @classmethod
    def load(cls, env_path: Path | None = None) -> "Config":
        _load_dotenv(env_path)
        return cls(
            username=os.getenv("LMS_USERNAME", "").strip(),
            password=os.getenv("LMS_PASSWORD", "").strip(),
            login_user_id=os.getenv("LMS_LOGIN_USER_ID", "").strip(),
            base_url=os.getenv("LMS_BASE_URL", "").strip(),
        )

    def require_credentials(self) -> None:
        missing = [
            name
            for name, value in (("LMS_USERNAME", self.username), ("LMS_PASSWORD", self.password))
            if not value
        ]
        if missing:
            raise SystemExit(
                "로그인 정보가 없습니다. .env 파일이나 환경변수에 다음을 설정하세요: "
                + ", ".join(missing)
            )


def _load_dotenv(env_path: Path | None) -> None:
    """Minimal .env loader (no external dependency). Existing env vars win."""
    candidates: list[Path] = []
    if env_path is not None:
        candidates.append(env_path)
    candidates.append(Path.cwd() / ".env")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        with candidate.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip only a single matching surrounding quote pair (don't mangle
                # passwords that legitimately contain quote characters).
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                # Existing env vars win — test presence, not truthiness, so an env var
                # explicitly set to "" is honored rather than overridden by .env.
                if value and key not in os.environ:
                    os.environ[key] = value
        return
