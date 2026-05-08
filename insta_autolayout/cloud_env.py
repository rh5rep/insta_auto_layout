from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, value.strip())


@dataclass(frozen=True, slots=True)
class SupabaseConfig:
    url: str
    publishable_key: str
    secret_key: str

    @property
    def api_key(self) -> str:
        return self.secret_key or self.publishable_key

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)


@dataclass(frozen=True, slots=True)
class R2Config:
    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    endpoint: str

    @property
    def configured(self) -> bool:
        return bool(self.account_id and self.bucket and self.access_key_id and self.secret_access_key and self.endpoint)


@lru_cache(maxsize=1)
def supabase_config() -> SupabaseConfig:
    _load_dotenv()
    return SupabaseConfig(
        url=os.environ.get("SUPABASE_URL", "").strip(),
        publishable_key=os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip(),
        secret_key=os.environ.get("SUPABASE_SECRET_KEY", "").strip(),
    )


@lru_cache(maxsize=1)
def r2_config() -> R2Config:
    _load_dotenv()
    return R2Config(
        account_id=os.environ.get("R2_ACCOUNT_ID", "").strip(),
        bucket=os.environ.get("R2_BUCKET", "").strip(),
        access_key_id=os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
        secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
        endpoint=os.environ.get("R2_ENDPOINT", "").strip(),
    )


def supabase_enabled() -> bool:
    return supabase_config().configured


def r2_enabled() -> bool:
    return r2_config().configured
