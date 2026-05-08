from __future__ import annotations

import hashlib
import hmac
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

from .cloud_env import r2_config, r2_enabled


class R2PublishError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedBatch:
    object_key: str
    download_url: str
    expires_at: str
    size_bytes: int


def r2_publish_available() -> bool:
    return r2_enabled()


def publish_batch_zip(batch_dir: Path, project_id: str, reviewer_id: str, expires_in_seconds: int = 7 * 24 * 60 * 60) -> PublishedBatch:
    config = r2_config()
    if not config.configured:
        raise R2PublishError("R2 is not configured")
    batch_dir = batch_dir.expanduser().resolve()
    if not batch_dir.exists() or not batch_dir.is_dir():
        raise R2PublishError(f"Batch directory does not exist: {batch_dir}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    object_key = f"published-batches/{project_id}/{batch_dir.name}/{timestamp}-{reviewer_id}.zip"
    with tempfile.NamedTemporaryFile(prefix="insta-batch-", suffix=".zip", delete=False) as handle:
        zip_path = Path(handle.name)
    try:
        _zip_batch(batch_dir, zip_path)
        data = zip_path.read_bytes()
        _put_object(config.endpoint, config.bucket, object_key, data, "application/zip", config.access_key_id, config.secret_access_key)
    finally:
        zip_path.unlink(missing_ok=True)

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    download_url = _presigned_get_url(
        endpoint=config.endpoint,
        bucket=config.bucket,
        object_key=object_key,
        access_key_id=config.access_key_id,
        secret_access_key=config.secret_access_key,
        expires_in=expires_in_seconds,
    )
    return PublishedBatch(
        object_key=object_key,
        download_url=download_url,
        expires_at=expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        size_bytes=len(data),
    )


def _zip_batch(batch_dir: Path, zip_path: Path) -> None:
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(batch_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(batch_dir)))


def _put_object(
    endpoint: str,
    bucket: str,
    object_key: str,
    data: bytes,
    content_type: str,
    access_key_id: str,
    secret_access_key: str,
) -> None:
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    parsed = urlparse(endpoint)
    host = parsed.netloc
    canonical_uri = f"/{bucket}/{quote(object_key, safe='/._-~')}"
    payload_hash = hashlib.sha256(data).hexdigest()
    headers = {
        "content-type": content_type,
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
    canonical_request = "\n".join(
        [
            "PUT",
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    authorization = _authorization_header(
        canonical_request=canonical_request,
        amz_date=amz_date,
        date_stamp=date_stamp,
        signed_headers=signed_headers,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    request = Request(
        f"{parsed.scheme}://{host}{canonical_uri}",
        data=data,
        method="PUT",
        headers={
            "Content-Type": content_type,
            "Authorization": authorization,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
        },
    )
    try:
        with urlopen(request, timeout=60):
            return
    except Exception as exc:  # pragma: no cover - network-dependent
        raise R2PublishError(f"R2 upload failed: {exc}") from exc


def _presigned_get_url(
    *,
    endpoint: str,
    bucket: str,
    object_key: str,
    access_key_id: str,
    secret_access_key: str,
    expires_in: int,
) -> str:
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    parsed = urlparse(endpoint)
    host = parsed.netloc
    canonical_uri = f"/{bucket}/{quote(object_key, safe='/._-~')}"
    credential_scope = f"{date_stamp}/auto/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key_id}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(max(1, min(expires_in, 604800))),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{quote(key, safe='')}={quote(value, safe='-_.~')}" for key, value in sorted(params.items()))
    canonical_request = "\n".join(
        [
            "GET",
            canonical_uri,
            canonical_query,
            f"host:{host}\n",
            "host",
            "UNSIGNED-PAYLOAD",
        ]
    )
    signature = _signature(
        canonical_request=canonical_request,
        amz_date=amz_date,
        date_stamp=date_stamp,
        secret_access_key=secret_access_key,
    )
    return f"{parsed.scheme}://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


def _authorization_header(
    *,
    canonical_request: str,
    amz_date: str,
    date_stamp: str,
    signed_headers: str,
    access_key_id: str,
    secret_access_key: str,
) -> str:
    credential_scope = f"{date_stamp}/auto/s3/aws4_request"
    signature = _signature(
        canonical_request=canonical_request,
        amz_date=amz_date,
        date_stamp=date_stamp,
        secret_access_key=secret_access_key,
    )
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def _signature(*, canonical_request: str, amz_date: str, date_stamp: str, secret_access_key: str) -> str:
    credential_scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _signing_key(secret_access_key, date_stamp)
    return hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _signing_key(secret_access_key: str, date_stamp: str) -> bytes:
    date_key = _hmac(("AWS4" + secret_access_key).encode("utf-8"), date_stamp)
    region_key = _hmac(date_key, "auto")
    service_key = _hmac(region_key, "s3")
    return _hmac(service_key, "aws4_request")


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
