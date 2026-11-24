"""码头锁与争用登记（写入路径嵌入）。

锁的获取/释放走独立短事务并立即提交，使 pier_locks 持锁行独立于业务事务存活：
后到者在持锁行存在期间抢锁必判争用，不与持锁者的业务提交时机竞态。
业务事务（write_connect, BEGIN IMMEDIATE）只负责业务写入的原子性与整笔回滚。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def try_acquire(conn, lock_key: str, holder: str) -> bool:
    """原子抢锁：INSERT OR IGNORE 杜绝“先 SELECT 后 INSERT”的窗口。

    调用方须自行保证本连接的事务提交；争用痕与抢锁结果同事务落库。
    """
    now = now_iso()
    cur = conn.execute(
        "INSERT OR IGNORE INTO pier_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
        (lock_key, holder, now),
    )
    if cur.rowcount == 1:
        return True
    row = conn.execute(
        "SELECT holder FROM pier_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (lock_key, holder, "争用", f"持有者={row['holder'] if row else '未知'}", now),
    )
    return False


def acquire_lock(lock_key: str, holder: str) -> bool:
    """独立短事务抢锁并立即提交；失败则争用痕同事务落库。"""
    with connect() as conn:
        ok = try_acquire(conn, lock_key, holder)
        conn.commit()
        return ok


def require_lock_key(lock_key: str, holder: str) -> None:
    if not acquire_lock(lock_key, holder):
        raise HTTPException(409, "争用：后到只记争用，不写业务")


def release_lock(lock_key: str, holder: str) -> None:
    """独立短事务释放锁并立即提交。"""
    with connect() as conn:
        conn.execute(
            "DELETE FROM pier_locks WHERE lock_key=? AND holder=?",
            (lock_key, holder),
        )
        conn.commit()


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pier_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock_endpoint(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    ok = acquire_lock(body.lock_key, user)
    with connect() as conn:
        write_audit(conn, user, "lock_acquire", body.lock_key, {"ok": ok})
        conn.commit()
    if not ok:
        raise HTTPException(409, "争用：未能获得锁")
    return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock_endpoint(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    release_lock(body.lock_key, user)
    with connect() as conn:
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
    return {"ok": True}
