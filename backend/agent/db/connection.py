"""
공통 PostgreSQL 커넥션 풀.

- DB_HOST 환경 변수가 설정된 경우에만 활성화
- ThreadedConnectionPool(min=2, max=10) 으로 멀티스레드 안전
- get_conn() 으로 풀에서 커넥션을 꺼내 사용 후 close() → 풀에 반납
"""
from __future__ import annotations

import os
from threading import Lock
from typing import Any, Optional


def is_db_configured() -> bool:
    """DB_HOST 환경 변수가 설정되어 있으면 True."""
    return bool((os.environ.get("DB_HOST") or "").strip())


# ── 커넥션 풀 ─────────────────────────────────────────────────────────────────
_pool: Optional[Any] = None
_pool_lock = Lock()


def _get_pool() -> Any:
    """스레드 안전 커넥션 풀 (더블 체크 락킹)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import psycopg2.pool
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=30,
                    host=os.environ.get("DB_HOST", "localhost"),
                    port=int(os.environ.get("DB_PORT", "5432")),
                    dbname=os.environ.get("DB_NAME", "aiops"),
                    user=os.environ.get("DB_USER", "aiops"),
                    password=os.environ.get("DB_PASSWORD", ""),
                    connect_timeout=10,
                )
    return _pool


class _PooledConn:
    """커넥션 풀 반환용 래퍼 — close() 시 pool.putconn() 으로 반납."""
    __slots__ = ("_conn", "_pool")

    def __init__(self, conn: Any, pool: Any) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_conn"), name)

    def close(self) -> None:
        object.__getattribute__(self, "_pool").putconn(
            object.__getattribute__(self, "_conn")
        )

    def __enter__(self) -> Any:
        return object.__getattribute__(self, "_conn").__enter__()

    def __exit__(self, *args: Any) -> Any:
        return object.__getattribute__(self, "_conn").__exit__(*args)


def get_conn() -> _PooledConn:
    """풀에서 커넥션을 꺼내 래퍼로 반환. close() 호출 시 자동 반납."""
    pool = _get_pool()
    return _PooledConn(pool.getconn(), pool)
