from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.soul_context import SOUL_PATH


SOUL_JOB_TYPES = {"profile_memory", "project_lifecycle"}


@dataclass(frozen=True)
class SoulSyncRetryResult:
    payload: dict[str, Any]


def enqueue_soul_sync_retry(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    job_type: str,
    source_table: str | None,
    source_id: int | None,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> int:
    _ = (db_path, source_table, source_id, payload, error)
    if job_type not in SOUL_JOB_TYPES:
        raise ValueError(f"unsupported_soul_sync_job_type:{job_type}")
    return 0


def get_soul_sync_status(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        counts = repo.soul_sync_retry_status_counts(connection)
        return {
            "counts": {
                "pending": counts.get("pending", 0),
                "retrying": counts.get("retrying", 0),
                "failed": counts.get("failed", 0),
                "succeeded": counts.get("succeeded", 0),
            },
            "recent_jobs": repo.list_recent_soul_sync_retry_jobs(connection, limit=10),
        }
    finally:
        connection.close()


def retry_soul_sync_jobs(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    soul_path: str | Path = SOUL_PATH,
    limit: int = 20,
) -> SoulSyncRetryResult:
    connection = initialize_database(db_path)
    try:
        jobs = repo.list_soul_sync_retry_jobs(connection, statuses=("pending", "failed"), limit=limit)
    finally:
        connection.close()

    results: list[dict[str, Any]] = []
    for job in jobs:
        results.append(_retry_one_job(db_path, job, soul_path=Path(soul_path)))
    status = get_soul_sync_status(db_path)
    return SoulSyncRetryResult(
        {
            "retried": len(results),
            "results": results,
            "status": status,
        }
    )


def _retry_one_job(db_path: str | Path, job: dict[str, Any], *, soul_path: Path) -> dict[str, Any]:
    job_id = int(job["id"])
    attempts = int(job.get("attempts") or 0) + 1
    _update_job(db_path, job_id, status="retrying", attempts=attempts)
    try:
        result = _run_job(db_path, job, soul_path=soul_path)
    except Exception as exc:  # noqa: BLE001 - keep retry state inspectable
        error = _safe_error(exc)
        _update_job(
            db_path,
            job_id,
            status="failed",
            attempts=attempts,
            last_error=error,
        )
        return {"id": job_id, "status": "failed", "reason": error}

    payload = dict(job.get("payload") or {})
    payload["disabled_reason"] = result["reason"]
    _update_job(
        db_path,
        job_id,
        status="succeeded",
        attempts=attempts,
        last_error=None,
        payload=payload,
    )
    return {"id": job_id, **result}


def _run_job(db_path: str | Path, job: dict[str, Any], *, soul_path: Path) -> dict[str, Any]:
    _ = (db_path, soul_path)
    job_type = str(job.get("job_type") or "")
    if job_type in SOUL_JOB_TYPES:
        return {
            "status": "skipped",
            "job_type": job_type,
            "reason": "soul_sync_retry_disabled",
        }
    raise ValueError(f"unsupported_soul_sync_job_type:{job_type}")


def _update_job(db_path: str | Path, job_id: int, **changes: Any) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.update_soul_sync_retry_job(connection, job_id, **changes)
    finally:
        connection.close()


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__
