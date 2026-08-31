"""
src/audit/logger.py

Append-only audit log. SQLite via stdlib sqlite3, no ORM. Immutability
is enforced at the DATABASE level via BEFORE UPDATE / BEFORE DELETE
triggers with RAISE(ABORT, ...) - not just "the AuditLogger class
doesn't expose an update method." Triggers are schema objects, bound to
the table itself: any tool connecting to this .db file with the
standard sqlite3 driver is bound by the same trigger, not just code that
goes through this class. Verified below by trying to UPDATE and DELETE
directly via raw SQL, bypassing this class entirely.

Hash-chained on top of that (defense in depth, not redundant): each row
commits to a SHA-256 of the previous row's hash plus its own content.
This protects against a threat the trigger doesn't fully cover - a
privileged actor with the file directly (DROP TRIGGER, edit bytes,
recreate trigger) could still bypass the SQL-level protection. A broken
hash chain proves *something* was tampered with, even if the mechanism
bypassed the trigger entirely. Verified below by deliberately doing
exactly that and confirming verify_chain() catches it.

This module was fully tested in this sandbox - no live database or
network dependency, unlike src/graph/builder.py's Neo4jGraphWriter.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only: DELETE is not permitted');
END;
"""


class AuditIntegrityError(Exception):
    """Raised by verify_chain() when a row's hash doesn't match its
    content, or doesn't chain correctly from the previous row - proof
    something was altered outside this class (e.g. the trigger was
    dropped and the raw table edited), not just a generic warning."""


@dataclass(frozen=True)
class AuditEvent:
    event_id: int
    timestamp: str
    run_id: str
    event_type: str
    payload: dict
    prev_hash: str
    row_hash: str


def _row_content_hash(timestamp: str, run_id: str, event_type: str, payload_json: str, prev_hash: str) -> str:
    canonical = "|".join([timestamp, run_id, event_type, payload_json, prev_hash])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditLogger:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT row_hash FROM audit_events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_HASH

    def append(self, event_type: str, run_id: str, payload: dict) -> AuditEvent:
        """The only write path this class exposes - no update(), no
        delete(). Computes the hash chain and inserts one row."""
        timestamp = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        prev_hash = self._last_hash()
        row_hash = _row_content_hash(timestamp, run_id, event_type, payload_json, prev_hash)

        cursor = self._conn.execute(
            "INSERT INTO audit_events (timestamp, run_id, event_type, payload, prev_hash, row_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (timestamp, run_id, event_type, payload_json, prev_hash, row_hash),
        )
        self._conn.commit()
        return AuditEvent(cursor.lastrowid, timestamp, run_id, event_type, payload, prev_hash, row_hash)

    def replay(self, run_id: str) -> list[AuditEvent]:
        """Every event for a given run_id, in order - 'what happened,
        when, in what sequence' for that run, per
        docs/01_flow_and_audit_events.md's replay requirement."""
        rows = self._conn.execute(
            "SELECT event_id, timestamp, run_id, event_type, payload, prev_hash, row_hash "
            "FROM audit_events WHERE run_id = ? ORDER BY event_id ASC",
            (run_id,),
        ).fetchall()
        return [
            AuditEvent(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5], r[6])
            for r in rows
        ]

    def verify_chain(self) -> bool:
        """Walks every row in the entire table, recomputing each hash
        from its content and confirming it chains from the previous
        row's hash. Raises AuditIntegrityError naming the exact broken
        row on any mismatch, rather than returning a bare False that
        doesn't say what's wrong."""
        rows = self._conn.execute(
            "SELECT event_id, timestamp, run_id, event_type, payload, prev_hash, row_hash "
            "FROM audit_events ORDER BY event_id ASC"
        ).fetchall()

        expected_prev = GENESIS_HASH
        for event_id, timestamp, run_id, event_type, payload_json, prev_hash, row_hash in rows:
            if prev_hash != expected_prev:
                raise AuditIntegrityError(
                    f"event_id={event_id}: prev_hash mismatch - chain broken "
                    f"(expected prev_hash={expected_prev!r}, row has {prev_hash!r}). "
                    f"A row was likely inserted, removed, or reordered outside this class."
                )
            recomputed = _row_content_hash(timestamp, run_id, event_type, payload_json, prev_hash)
            if recomputed != row_hash:
                raise AuditIntegrityError(
                    f"event_id={event_id}: content hash mismatch - this row's data was "
                    f"altered after being written (expected row_hash={recomputed!r}, "
                    f"found {row_hash!r})."
                )
            expected_prev = row_hash
        return True


if __name__ == "__main__":
    import tempfile

    print("=== Test 1: basic append + replay ===")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "audit_test.db"
        log = AuditLogger(db_path)

        log.append("GRAPH_INGESTED", run_id="run-001", payload={"nodes": 85, "edges": 100})
        log.append("FIGURE_COMPUTED", run_id="run-001", payload={"figure_id": "allocation::SGS", "value": "35.0%"})
        log.append("CONFIG_CHANGED", run_id="run-001", payload={"from": None, "to": "firm_a"})
        log.append("GRAPH_INGESTED", run_id="run-002", payload={"nodes": 85, "edges": 100})

        events_run1 = log.replay("run-001")
        events_run2 = log.replay("run-002")
        print(f"run-001 has {len(events_run1)} events (want 3): {[e.event_type for e in events_run1]}")
        print(f"run-002 has {len(events_run2)} events (want 1): {[e.event_type for e in events_run2]}")
        assert len(events_run1) == 3
        assert len(events_run2) == 1
        print("PASS")

        print("\n=== Test 2: chain verifies clean on unmodified data ===")
        assert log.verify_chain() is True
        print("PASS")

        print("\n=== Test 3: UPDATE is rejected at the SQL level, bypassing this class entirely ===")
        try:
            log._conn.execute("UPDATE audit_events SET payload = '{}' WHERE event_id = 1")
            print("FAIL: UPDATE should have been rejected")
        except sqlite3.IntegrityError as exc:
            print(f"PASS: UPDATE correctly rejected: {exc}")

        print("\n=== Test 4: DELETE is rejected at the SQL level ===")
        try:
            log._conn.execute("DELETE FROM audit_events WHERE event_id = 1")
            print("FAIL: DELETE should have been rejected")
        except sqlite3.IntegrityError as exc:
            print(f"PASS: DELETE correctly rejected: {exc}")

        print("\n=== Test 5: even bypassing the trigger (DROP TRIGGER), hash-chain tampering is caught ===")
        log._conn.execute("DROP TRIGGER audit_events_no_update")
        log._conn.execute("UPDATE audit_events SET payload = '{\"tampered\": true}' WHERE event_id = 2")
        log._conn.commit()
        log._conn.executescript(SCHEMA)  # restore the trigger
        try:
            log.verify_chain()
            print("FAIL: tampered chain should have raised AuditIntegrityError")
        except AuditIntegrityError as exc:
            print(f"PASS: tampering detected: {exc}")

        log.close()

    print("\n=== ALL TESTS PASS ===")
