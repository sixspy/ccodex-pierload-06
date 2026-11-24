"""停用禁磅第6号（车道案己）—— 三红测试。

口径（甲路径，当日锁死，禁止旁门）：
  甲 车道已停用则过磅写口零增并整笔回滚；停用只许留停用禁磅纪要，不设乙路径影子票。
  争用 两人同车道抢建票须抢车道锁，后到只记争用痕（409，不写业务）。
  对账 停用车道流量零计入，五页同态以库内车道状态为唯一口径。

第6路：争用测对真实 uvicorn 进程打两路并发请求（同进程 TestClient 会被门户串行化，
复现不了嵌入式车道锁的 BUSY 竞态）。
"""
from __future__ import annotations

import threading
import tempfile
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

import db
from app import app
from db import init_db

AXLES = [{"axle": 1, "w": 9.0}, {"axle": 2, "w": 10.0}]


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """每测一个全新库；服务端 connect() 实时读 db.DB_PATH。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    init_db()
    yield


@pytest.fixture(scope="module")
def live_server():
    """第6路：模块级真实 uvicorn，端口 0 自动分配。"""
    db.DB_PATH = Path(tempfile.mkdtemp()) / "server.db"
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def _auth(c, user="admin", password="admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _lane_id(c, h, code: str) -> int:
    rows = c.get("/api/lanes", headers=h).json()["items"]
    return next(x["id"] for x in rows if x["lane_code"] == code)


def _ticket_payload(lane_id: int, code: str, bl: str = "BL-2026-0101") -> dict:
    return {
        "lane_id": lane_id,
        "bl_ref": bl,
        "vehicle_plate": f"测F·{code}",
        "axle_weights": AXLES,
        "ticket_code": code,
    }


# ---------------------------------------------------------------------------
# 失败测甲：锁定停用仍建正式票（甲路径——停用即零增回滚，禁出正式票）
# ---------------------------------------------------------------------------
def test_甲_停用车道禁过磅_零增整笔回滚_纪要当日锁死():
    c = TestClient(app)
    h = _auth(c)

    # 停用车道种子夹具：LN-F03 生来停用（假绿样板），须有第6号锁死纪要背书
    lanes = c.get("/api/lanes", headers=h).json()["items"]
    f03 = next(x for x in lanes if x["lane_code"] == "LN-F03")
    assert f03["status"] == "停用"

    rulings = c.get("/api/rulings", headers=h).json()["items"]
    r6 = next(x for x in rulings if x["ruling_no"] == "停用禁磅第6号")
    assert r6["lane_code"] == "LN-F03"
    assert r6["path"] == "甲"
    assert r6["status"] == "当日锁死"

    # 经正式写口停用一条在运车道：裁决落库即锁死
    lane_id = _lane_id(c, h, "LN-N-02-IN")
    resp = c.patch(
        f"/api/lanes/{lane_id}/deactivate",
        headers=h,
        json={"note": "第6号案己：施工停用禁磅"},
    )
    assert resp.status_code == 200
    ruling = resp.json()
    assert ruling["path"] == "甲"
    assert ruling["status"] == "当日锁死"
    assert c.get(f"/api/lanes/{lane_id}", headers=h).json()["lane"]["status"] == "停用"

    # 纪要当日锁死：重复裁决拒写，不得产生第二份纪要
    again = c.patch(
        f"/api/lanes/{lane_id}/deactivate", headers=h, json={"note": "试图改判"}
    )
    assert again.status_code == 409
    assert "锁死" in again.json()["detail"]

    with db.connect() as conn:
        before = {
            "tickets": conn.execute(
                "SELECT COUNT(*) c FROM weigh_tickets"
            ).fetchone()["c"],
            "holds": conn.execute(
                "SELECT COUNT(*) c FROM overload_holds"
            ).fetchone()["c"],
            "releases": conn.execute(
                "SELECT COUNT(*) c FROM gate_releases"
            ).fetchone()["c"],
            "rulings": conn.execute(
                "SELECT COUNT(*) c FROM disable_rulings WHERE lane_id=?",
                (lane_id,),
            ).fetchone()["c"],
        }

    # 过磅写口打停用车道：409 拒写，正式票零增
    blocked = c.post(
        "/api/tickets", headers=h, json=_ticket_payload(lane_id, "WT-F03-BLOCK-1")
    )
    assert blocked.status_code == 409
    assert "停用禁磅" in blocked.json()["detail"]

    # 种子停用车道 LN-F03 同样拒写（假绿夹具守卫）
    blocked_f03 = c.post(
        "/api/tickets", headers=h, json=_ticket_payload(f03["id"], "WT-F03-BLOCK-2")
    )
    assert blocked_f03.status_code == 409
    assert "停用禁磅" in blocked_f03.json()["detail"]

    with db.connect() as conn:
        after = {
            "tickets": conn.execute(
                "SELECT COUNT(*) c FROM weigh_tickets"
            ).fetchone()["c"],
            "holds": conn.execute(
                "SELECT COUNT(*) c FROM overload_holds"
            ).fetchone()["c"],
            "releases": conn.execute(
                "SELECT COUNT(*) c FROM gate_releases"
            ).fetchone()["c"],
        }
        ghost = conn.execute(
            "SELECT id FROM weigh_tickets WHERE ticket_code IN ('WT-F03-BLOCK-1','WT-F03-BLOCK-2')"
        ).fetchall()
        rulings_after = conn.execute(
            "SELECT COUNT(*) c FROM disable_rulings WHERE lane_id=?", (lane_id,)
        ).fetchone()["c"]
    assert after["tickets"] == before["tickets"]
    assert after["holds"] == before["holds"]
    assert after["releases"] == before["releases"]
    assert ghost == []
    assert rulings_after == before["rulings"] == 1  # 纪要只准一份

    # 偏离纪要须记停用禁磅偏航痕
    audit = c.get("/api/audit?limit=500", headers=h).json()["items"]
    yaws = [a for a in audit if a["action"] == "停用禁磅偏航"]
    assert {a["target"] for a in yaws} >= {"LN-N-02-IN", "LN-F03"}


# ---------------------------------------------------------------------------
# 失败测乙：锁定对账仍计该车道流量（停用车道流量零计入）
# ---------------------------------------------------------------------------
def test_乙_对账不计停用车道流量():
    c = TestClient(app)
    h = _auth(c)
    bl = "BL-2026-0101"

    before = c.get(f"/api/reconcile/{bl}", headers=h).json()
    assert before["ticket_sum_t"] == pytest.approx(38.1)
    assert any(t["ticket_code"] == "WT-LN-N-01-IN-001" for t in before["tickets"])

    # 直接置停用（绕开写口，专测对账口径），模拟车道页停用而对账仍计流量的病态
    with db.connect() as conn:
        conn.execute(
            "UPDATE weigh_lanes SET status='停用' WHERE lane_code='LN-N-01-IN'"
        )
        conn.commit()

    after = c.get(f"/api/reconcile/{bl}", headers=h).json()
    assert after["ticket_sum_t"] == 0
    assert after["tickets"] == []
    excluded = {x["ticket_code"] for x in after["deactivated_excluded"]}
    assert "WT-LN-N-01-IN-001" in excluded

    # 控制组：在运车道流量照计，口径只按库内车道状态切换
    control = c.get("/api/reconcile/BL-2026-0102", headers=h).json()
    assert control["ticket_sum_t"] == pytest.approx(12.0)
    assert control["tickets"]


# ---------------------------------------------------------------------------
# 争用失败测：第6路两路并发请求打同车道建票，后到只记争用痕。
# SQLite 单写者会把无重叠窗口的并发请求串行化，故先经正式抢锁口占住车道锁
# （持锁行在独立短事务中持续存活），再两路并发打建票，确定性逼出争用判定。
# ---------------------------------------------------------------------------
def test_争用_两路并发同车道抢建_后到只记争用痕(live_server):
    base = live_server
    with httpx.Client() as client:
        r1 = client.post(
            base + "/api/login", json={"username": "admin", "password": "admin123"}
        )
        r2 = client.post(
            base + "/api/login", json={"username": "writer", "password": "writer123"}
        )
        h_admin = {"Authorization": "Bearer " + r1.json()["token"]}
        h_writer = {"Authorization": "Bearer " + r2.json()["token"]}
        lanes = client.get(base + "/api/lanes", headers=h_admin).json()["items"]
        lane = next(x for x in lanes if x["lane_code"] == "LN-S-01-OUT")
        lock_key = f"lane:{lane['id']}"

        # 先经正式抢锁口占住车道锁（持锁行独立于业务事务持续存活）
        occ = client.post(
            base + "/api/locks/acquire", headers=h_admin, json={"lock_key": lock_key}
        )
        assert occ.status_code == 200

        results: dict = {}
        barrier = threading.Barrier(2)

        def fire(name, headers, code):
            barrier.wait()
            r = client.post(
                base + "/api/tickets",
                headers=headers,
                json=_ticket_payload(lane["id"], code, bl="BL-2026-0102"),
            )
            results[name] = (r.status_code, r.text[:160])

        t1 = threading.Thread(
            target=fire, args=("admin", h_admin, "WT-CONTEND-A")
        )
        t2 = threading.Thread(
            target=fire, args=("writer", h_writer, "WT-CONTEND-B")
        )
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 持锁期间两路建票皆拒，业务零写
        statuses = sorted(v[0] for v in results.values())
        assert statuses == [409, 409], f"持锁期内须双拒，实得 {results}"
        for v in results.values():
            assert "争用" in v[1]

        with db.connect() as conn:
            written = conn.execute(
                "SELECT ticket_code FROM weigh_tickets WHERE ticket_code IN ('WT-CONTEND-A','WT-CONTEND-B')"
            ).fetchall()
            contends = conn.execute(
                "SELECT actor, action FROM contention_log WHERE lock_key=?", (lock_key,)
            ).fetchall()
        assert written == []
        assert len(contends) == 2
        assert {r["actor"] for r in contends} == {"admin", "writer"}
        assert all(r["action"] == "争用" for r in contends)

        # 释放持锁后，同车道建票即恢复：证明锁机制真实拦截而非接口恒拒
        rel = client.post(
            base + "/api/locks/release", headers=h_admin, json={"lock_key": lock_key}
        )
        assert rel.status_code == 200
        ok = client.post(
            base + "/api/tickets",
            headers=h_writer,
            json=_ticket_payload(lane["id"], "WT-CONTEND-OK", bl="BL-2026-0102"),
        )
        assert ok.status_code == 200

        with db.connect() as conn:
            locks_left = conn.execute(
                "SELECT COUNT(*) c FROM pier_locks WHERE lock_key=?", (lock_key,)
            ).fetchone()["c"]
    assert locks_left == 0  # 胜者事毕释放，不留残锁
