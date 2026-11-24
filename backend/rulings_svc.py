"""停用禁磅纪要（裁决落库，当日锁死）。

口径唯一（甲路径）：车道已停用则过磅写口零增并整笔回滚；停用只许留纪要，
禁止乙路径影子挂起票等旁门写法。纪要只增不改不删，同车道重复裁决拒写。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import (
    RULING_SERIES_START,
    connect,
    now_iso,
    write_audit,
    write_connect,
)
from locks import release_lock, require_lock_key

router = APIRouter(tags=["rulings"])

RULING_PATH = "甲"
RULING_LOCKED = "当日锁死"


class LaneDeactivateIn(BaseModel):
    note: str = "车道停用禁磅"


def next_ruling_no(conn) -> str:
    n = conn.execute("SELECT COUNT(*) c FROM disable_rulings").fetchone()["c"]
    return f"停用禁磅第{RULING_SERIES_START + n}号"


def lane_ruling(conn, lane_id: int):
    return conn.execute(
        "SELECT * FROM disable_rulings WHERE lane_id=? ORDER BY id DESC LIMIT 1",
        (lane_id,),
    ).fetchone()


@router.get("/api/rulings")
def list_rulings(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM disable_rulings ORDER BY id DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/rulings/lane/{lane_id}")
def get_lane_ruling(lane_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = lane_ruling(conn, lane_id)
        if not row:
            raise HTTPException(404, "该车道无停用禁磅纪要")
        return dict(row)


@router.patch("/api/lanes/{lane_id}/deactivate")
def deactivate_lane(lane_id: int, body: LaneDeactivateIn, authorization: AuthHeader = None):
    """停用车道并落停用禁磅纪要；与过磅写口抢同一把车道锁。"""
    user = require_writer(authorization)
    lock_key = f"lane:{lane_id}"
    require_lock_key(lock_key, user)
    try:
        with write_connect() as conn:
            lane = conn.execute(
                "SELECT * FROM weigh_lanes WHERE id=?", (lane_id,)
            ).fetchone()
            if not lane:
                raise HTTPException(404, "车道不存在")
            if lane["status"] == "停用":
                # 纪要当日锁死：重复裁决拒写，不得改判或新增第二份
                raise HTTPException(409, "车道已停用，停用禁磅纪要当日锁死，禁止改判")
            ruling = lane_ruling(conn, lane_id)
            if ruling:
                raise HTTPException(409, "停用禁磅纪要已存在且锁死，禁止重复裁决")
            now = now_iso()
            ruling_no = next_ruling_no(conn)
            cur = conn.execute(
                """INSERT INTO disable_rulings(ruling_no,lane_id,lane_code,path,note,
                   actor,status,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    ruling_no,
                    lane_id,
                    lane["lane_code"],
                    RULING_PATH,
                    body.note,
                    user,
                    RULING_LOCKED,
                    now,
                ),
            )
            conn.execute(
                "UPDATE weigh_lanes SET status='停用' WHERE id=?", (lane_id,)
            )
            write_audit(
                conn,
                user,
                "lane_deactivate",
                lane["lane_code"],
                {
                    "ruling_no": ruling_no,
                    "path": RULING_PATH,
                    "status": RULING_LOCKED,
                },
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "ruling_no": ruling_no,
                "lane_id": lane_id,
                "lane_code": lane["lane_code"],
                "path": RULING_PATH,
                "status": RULING_LOCKED,
            }
    finally:
        release_lock(lock_key, user)
