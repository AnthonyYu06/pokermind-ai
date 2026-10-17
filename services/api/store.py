"""SQLite projections and immutable original files; all review paths share require_hand."""
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from packages.ignition_import.parser import PARSER_VERSION, parse
from packages.pokermind_core.contracts import Hand, canonical, digest
from packages.pokermind_core.eligibility import POLICY_VERSION, assess, utcnow
from packages.pokermind_core.engine import reconstruct
from packages.simulation.generate import GENERATOR, generate

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ingest_files(
 file_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, source_kind TEXT NOT NULL,
 original_name TEXT NOT NULL, parser_version TEXT NOT NULL, import_time TEXT NOT NULL,
 policy_version TEXT NOT NULL, status TEXT NOT NULL, report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_records(
 file_id TEXT NOT NULL REFERENCES ingest_files(file_id), line INTEGER NOT NULL,
 byte_start INTEGER, byte_end INTEGER, status TEXT NOT NULL, reason TEXT NOT NULL,
 canonical TEXT, content_hash TEXT, PRIMARY KEY(file_id,line));
CREATE TABLE IF NOT EXISTS hands(
 hand_id TEXT PRIMARY KEY, source_hand_id TEXT NOT NULL, source_file_id TEXT NOT NULL REFERENCES ingest_files(file_id),
 session_id TEXT NOT NULL, source_kind TEXT NOT NULL, content_hash TEXT NOT NULL,
 canonical TEXT NOT NULL, quality TEXT NOT NULL, declaration TEXT, hero INTEGER);
CREATE INDEX IF NOT EXISTS hands_session ON hands(source_kind,session_id);
CREATE TABLE IF NOT EXISTS decision_observations(
 decision_id TEXT PRIMARY KEY, hand_id TEXT NOT NULL REFERENCES hands(hand_id),
 action_index INTEGER NOT NULL, observation_hash TEXT NOT NULL, observation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_results(
 decision_id TEXT PRIMARY KEY REFERENCES decision_observations(decision_id),
 model_version TEXT NOT NULL, data_hash TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_analysis_cache(
 cache_key TEXT PRIMARY KEY,decision_id TEXT NOT NULL REFERENCES decision_observations(decision_id),
 created_at TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS study_queue(
 decision_id TEXT PRIMARY KEY REFERENCES decision_observations(decision_id),
 created_at TEXT NOT NULL, due_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS practice_sessions(
 attempt_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decision_observations(decision_id),
 started_at TEXT NOT NULL, completed_at TEXT, response TEXT, feedback TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL, event TEXT NOT NULL, record_id TEXT NOT NULL);
"""


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "originals").mkdir(exist_ok=True, mode=0o700)
        self.db = self.root / "pokermind.sqlite3"
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)", (utcnow().isoformat(),))
        from .projections import migrate
        migrate(self)
        from .import_jobs import initialize as initialize_imports
        initialize_imports(self)
        from .privacy import initialize
        initialize(self)
        with self.connect() as conn: conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES (5,?)", (utcnow().isoformat(),))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA secure_delete=ON")
        try:
            with conn: yield conn
        finally: conn.close()

    def audit(self, conn, event, record_id):
        conn.execute("INSERT INTO audit(at,event,record_id) VALUES (?,?,?)", (utcnow().isoformat(), event, record_id))

    def preview(self, data: bytes, name: str):
        # External callers cannot choose synthetic provenance.
        return self._stage(data, name, "real")

    def _stage(self, data, name, source, *, check=None, progress=None):
        parsed = parse(data, check=check, progress=progress)
        file_hash = hashlib.sha256(data).hexdigest()
        file_id = digest([file_hash, source, PARSER_VERSION])
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM ingest_files WHERE file_id=?", (file_id,)).fetchone()
            if existing and existing["status"] not in ("cancelled", "queued"): return json.loads(existing["report"])
            target = self.root / "originals" / file_hash
            if not target.exists():
                temp = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
                with temp.open("xb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp, target)
                target.chmod(0o400)
            diagnostics = []
            counts = {k: 0 for k in ("ready", "imported", "duplicate", "unsupported", "quarantined", "failed", "waiting")}
            seen = {}
            for rec in parsed:
                if check: check()
                status, reason = rec.status, rec.reason
                if rec.hand and status == "valid":
                    hid = self.hand_id(rec.hand, source)
                    old = conn.execute("SELECT content_hash FROM hands WHERE hand_id=?", (hid,)).fetchone()
                    prior = old[0] if old else seen.get(hid)
                    if prior:
                        status = "duplicate" if prior == rec.content_hash else "quarantined"
                        reason = "Previously imported hand" if status == "duplicate" else "Conflicting revision; original retained for audit"
                    else:
                        policy = assess(rec.hand.timestamps, source)
                        status = "ready" if policy.eligible else policy.status
                        reason = policy.reason
                    seen[hid] = rec.content_hash
                counts[status] += 1
                diagnostics.append({"line": rec.line, "byte_start": rec.byte_start, "byte_end": rec.byte_end,
                                    "status": status, "reason": reason})
            report = {"file_id": file_id, "name": Path(name.replace("\\", "/")).name[:120], "content_hash": file_hash,
                      "source_kind": source, "parser_version": PARSER_VERSION, "policy_version": POLICY_VERSION,
                      "status": "preview", "counts": counts, "diagnostics": diagnostics}
            conn.execute("INSERT INTO ingest_files VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET status=excluded.status, report=excluded.report",
                         (file_id, file_hash, source, report["name"], PARSER_VERSION, utcnow().isoformat(), POLICY_VERSION, "preview", canonical(report)))
            for rec in parsed:
                conn.execute("INSERT OR REPLACE INTO import_records VALUES (?,?,?,?,?,?,?,?)",
                             (file_id, rec.line, rec.byte_start, rec.byte_end, rec.status, rec.reason,
                              canonical(rec.hand) if rec.hand else None, rec.content_hash))
            from .import_jobs import diagnostics as save_diagnostics
            save_diagnostics(conn, file_id, diagnostics)
            if check: check()
            self.audit(conn, "preview", file_id)
        return report

    @staticmethod
    def hand_id(hand, source):
        return digest([source, hand.session_id, hand.source_hand_id])[:24]

    def commit(self, file_id, *, check=None, progress=None):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            file = conn.execute("SELECT * FROM ingest_files WHERE file_id=?", (file_id,)).fetchone()
            if not file: raise KeyError("Import not found")
            if file["status"] == "committed": return json.loads(file["report"])
            if file["status"] != "preview": raise ValueError("Import was cancelled; preview again before committing")
            report = json.loads(file["report"])
            counts = {k: 0 for k in report["counts"]}
            diagnostics = []
            total = conn.execute("SELECT COUNT(*) FROM import_records WHERE file_id=?", (file_id,)).fetchone()[0]
            for number, rec in enumerate(conn.execute("SELECT * FROM import_records WHERE file_id=? ORDER BY line", (file_id,)), 1):
                if check: check()
                if progress: progress(number-1,total)
                status, reason = rec["status"], rec["reason"]
                if rec["canonical"]:
                    hand = Hand.model_validate_json(rec["canonical"])
                    hid = self.hand_id(hand, file["source_kind"])
                    old = conn.execute("SELECT content_hash FROM hands WHERE hand_id=?", (hid,)).fetchone()
                    if old:
                        status = "duplicate" if old[0] == rec["content_hash"] else "quarantined"
                        reason = "Hand already imported" if status == "duplicate" else "Conflicting revision; all versions blocked pending resolution"
                        if status == "quarantined":
                            conn.execute("UPDATE hands SET quality='conflicting_revision' WHERE hand_id=?", (hid,))
                            conn.execute("DELETE FROM analysis_results WHERE decision_id IN (SELECT decision_id FROM decision_observations WHERE hand_id=?)", (hid,))
                    else:
                        policy = assess(hand.timestamps, file["source_kind"])
                        quality = "valid" if rec["status"] == "valid" else rec["reason"]
                        status = ("imported" if policy.eligible else policy.status) if quality == "valid" else "quarantined"
                        reason = policy.reason if quality == "valid" else quality
                        conn.execute("INSERT INTO hands VALUES (?,?,?,?,?,?,?,?,?,?)",
                                     (hid, hand.source_hand_id, file_id, hand.session_id, file["source_kind"], rec["content_hash"], rec["canonical"], quality, None, hand.hero))
                        conn.execute("INSERT OR IGNORE INTO sessions VALUES (?,?,?)", (digest([file["source_kind"], hand.session_id])[:24], file["source_kind"], hand.session_id))
                        if quality == "valid":
                            from .projections import project
                            project(conn, hid, hand, file["source_kind"])
                            _, observations = reconstruct(hand)
                            for obs in observations:
                                did = f"{hid}:{obs['action_index']}"
                                conn.execute("INSERT INTO decision_observations VALUES (?,?,?,?,?)", (did, hid, obs["action_index"], obs["observation_hash"], canonical(obs)))
                counts[status] += 1
                diagnostics.append({"line": rec["line"], "byte_start": rec["byte_start"], "byte_end": rec["byte_end"], "status": status, "reason": reason})
            report.update(status="committed", counts=counts, diagnostics=diagnostics)
            conn.execute("UPDATE ingest_files SET status='committed',report=? WHERE file_id=?", (canonical(report), file_id))
            from .import_jobs import diagnostics as save_diagnostics
            save_diagnostics(conn, file_id, diagnostics)
            if progress: progress(total,total)
            if check: check()
            self.audit(conn, "commit", file_id)
        return report

    def cancel(self, file_id):
        with self.connect() as conn:
            row = conn.execute("SELECT report,status FROM ingest_files WHERE file_id=?", (file_id,)).fetchone()
            if not row: raise KeyError("Import not found")
            if row["status"] != "preview": raise ValueError("Only a preview can be cancelled")
            report = json.loads(row["report"])
            report["status"] = "cancelled"
            conn.execute("UPDATE ingest_files SET status='cancelled',report=? WHERE file_id=?", (canonical(report), file_id))
            self.audit(conn, "cancel", file_id)
            return report

    def seed_demo(self):
        hands = generate()
        data = ("\n".join(canonical(h) for h in hands) + "\n").encode()
        report = self._stage(data, "synthetic-demo.jsonl", "synthetic")
        return self.commit(report["file_id"])

    def eligibility(self, row):
        hand = Hand.model_validate_json(row["canonical"])
        policy = asdict(assess(hand.timestamps, row["source_kind"], declaration=row["declaration"]))
        if row["quality"] != "valid": policy.update(eligible=False, status="quarantined", reason=row["quality"])
        return policy

    def require_hand(self, hand_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM hands WHERE hand_id=?", (hand_id,)).fetchone()
        if not row: raise KeyError("Hand not found")
        if not self.eligibility(row)["eligible"]: raise PermissionError(self.eligibility(row)["reason"])
        return row, Hand.model_validate_json(row["canonical"])

    def hand_summary(self, row):
        h = Hand.model_validate_json(row["canonical"])
        policy = self.eligibility(row)
        return {"hand_id": row["hand_id"], "label": h.source_hand_id, "session_id": h.session_id,
                           "source_kind": row["source_kind"], "hero": row["hero"], "eligibility": policy,
                           "table_size": len(h.players), "big_blind": h.big_blind, "currency": h.currency,
                           "started_at": h.timestamps.started_at,
                           "net": (h.terminal_summary.final_stacks[row["hero"]] - next(p.stack for p in h.players if p.seat == row["hero"])) if row["hero"] is not None and policy["eligible"] else None}

    def iter_hands(self):
        with self.connect() as conn:
            for row in conn.execute("SELECT * FROM hands ORDER BY rowid DESC"):
                yield self.hand_summary(row)

    def list_hands(self):
        return list(self.iter_hands())

    def hand_page(self, source=None, query="", cursor=None, limit=50, session_key=None):
        if not 1 <= limit <= 100 or (cursor is not None and cursor < 1):
            raise ValueError("Invalid page size or cursor")
        if source not in (None, "real", "synthetic") or len(query) > 120:
            raise ValueError("Invalid hand filter")
        where, values = [], []
        if source:
            where.append("source_kind=?"); values.append(source)
        if query:
            where.append("instr(lower(source_hand_id || ' ' || session_id), lower(?)) > 0"); values.append(query)
        with self.connect() as conn:
            if session_key:
                session = conn.execute("SELECT * FROM sessions WHERE session_key=?", (session_key,)).fetchone()
                if not session: raise KeyError("Session not found")
                where.extend(["source_kind=?", "session_id=?"]); values.extend([session["source_kind"], session["session_id"]])
            clause = " WHERE " + " AND ".join(where) if where else ""
            total = conn.execute("SELECT COUNT(*) FROM hands" + clause, values).fetchone()[0]
            if cursor is not None:
                where.append("rowid < ?"); values.append(cursor)
            clause = " WHERE " + " AND ".join(where) if where else ""
            rows = conn.execute("SELECT rowid AS page_id,* FROM hands" + clause + " ORDER BY rowid DESC LIMIT ?", [*values, limit + 1]).fetchall()
        return {"items": [self.hand_summary(r) for r in rows[:limit]], "total": total,
                "next_cursor": rows[limit - 1]["page_id"] if len(rows) > limit else None}

    def overview(self):
        from packages.pokermind_core.metrics import summarize
        recent, counts = [], {}
        def eligible_rows():
            with self.connect() as conn:
                for row in conn.execute("SELECT * FROM hands ORDER BY rowid DESC"):
                    summary = self.hand_summary(row)
                    group = counts.setdefault(row["source_kind"], {"total": 0, "eligible": 0})
                    group["total"] += 1
                    group["eligible"] += int(summary["eligibility"]["eligible"])
                    if group["total"] <= 50: recent.append(summary)
                    if summary["eligibility"]["eligible"]:
                        yield row, Hand.model_validate_json(row["canonical"])
        stats = summarize(eligible_rows())
        return {"hands": recent, "counts": counts, "stats": stats, "progress": self.progress(),
                "pending": sum(g["total"] - g["eligible"] for g in counts.values()),
                "compatibility": "Ignition format unverified — sanitized exports required"}

    def declare(self, hand_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM hands WHERE hand_id=?", (hand_id,)).fetchone()
            if not row: raise KeyError("Hand not found")
            h = Hand.model_validate_json(row["canonical"])
            if h.timestamps.session_end: raise ValueError("Session already has end evidence; correct invalid source instead")
            # One timestamp per source-scoped session; repeats never shorten the wait.
            prior = conn.execute("SELECT declaration FROM hands WHERE source_kind=? AND session_id=? AND declaration IS NOT NULL LIMIT 1", (row["source_kind"], row["session_id"])).fetchone()
            declared = prior[0] if prior else utcnow().isoformat()
            conn.execute("UPDATE hands SET declaration=? WHERE source_kind=? AND session_id=? AND declaration IS NULL", (declared, row["source_kind"], row["session_id"]))
            self.audit(conn, "completed-session-declaration", hand_id)
        return {"declared_at": declared, "minimum_wait_hours": 24}

    def select_hero(self, hand_id, seat):
        row, hand = self.require_hand(hand_id)
        if seat not in [p.seat for p in hand.players]: raise ValueError("Hero must be a seated player")
        with self.connect() as conn:
            conn.execute("UPDATE hands SET hero=? WHERE hand_id=?", (seat, hand_id))
            self.audit(conn, "select-hero", hand_id)
        return {"hero": seat}

    def replay(self, hand_id, retrospective=False):
        row, hand = self.require_hand(hand_id)
        snapshots, observations = reconstruct(hand, viewer=row["hero"], retrospective=retrospective)
        return {"hand_id": hand_id, "source_kind": row["source_kind"], "label": hand.source_hand_id,
                "currency": hand.currency, "scale": hand.scale, "hero": row["hero"], "big_blind": hand.big_blind,
                "view": "retrospective" if retrospective else "decision-time", "snapshots": snapshots,
                "events": [e.model_dump() for e in hand.events],
                "decisions": [{"decision_id": f"{hand_id}:{o['action_index']}", "action_index": o["action_index"], "acting_player": o["acting_player"]} for o in observations],
                "eligibility": self.eligibility(row)}

    def decision(self, decision_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM decision_observations WHERE decision_id=?", (decision_id,)).fetchone()
        if not row: raise KeyError("Decision not found")
        _, hand = self.require_hand(row["hand_id"])
        return row, hand, json.loads(row["observation"])

    def bookmark(self, decision_id):
        self.decision(decision_id)
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO study_queue VALUES (?,?,?,0)", (decision_id, utcnow().isoformat(), utcnow().isoformat()))
        return {"saved": True}

    def queue(self):
        with self.connect() as conn: rows = conn.execute("SELECT * FROM study_queue ORDER BY due_at").fetchall()
        result = []
        for row in rows:
            try:
                _, _, obs = self.decision(row["decision_id"])
                result.append({**dict(row), "street": obs["state"]["street"]})
            except PermissionError: continue
        return result

    def start_practice(self):
        queued = self.queue()
        did = next((q["decision_id"] for q in queued if q["due_at"] <= utcnow().isoformat()), None)
        if did is None:
            with self.connect() as conn:
                rows = conn.execute("SELECT d.decision_id FROM decision_observations d JOIN hands h ON h.hand_id=d.hand_id WHERE h.source_kind='synthetic' ORDER BY RANDOM() LIMIT 200").fetchall()
            for row in rows:
                _, h, obs = self.decision(row[0])
                if obs["acting_player"] == h.hero and obs["state"]["street"] in ("flop", "turn", "river"):
                    did = row[0]
                    break
        if did is None: raise ValueError("Generate the demo or bookmark an eligible decision first")
        _, _, obs = self.decision(did)
        attempt_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO practice_sessions(attempt_id,decision_id,started_at) VALUES (?,?,?)", (attempt_id, did, utcnow().isoformat()))
        return {"attempt_id": attempt_id, "observation": obs, "topic": "Legal actions & pot-share intuition"}

    def submit_practice(self, attempt_id, kind, to, confidence):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute("SELECT * FROM practice_sessions WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not attempt: raise KeyError("Practice session not found")
            if attempt["completed_at"]: raise ValueError("Attempt already submitted")
            row, hand, obs = self.decision(attempt["decision_id"])
            legal = obs["state"]["legal_actions"]
            if kind not in legal: raise ValueError("Action is not legal in the server-owned decision")
            if kind in ("bet", "raise"):
                if to is None or not legal[kind]["min_to"] <= to <= legal[kind]["max_to"]: raise ValueError("Bet size is outside legal bounds")
            elif to is not None: raise ValueError("This action has no bet size")
            recorded = hand.events[row["action_index"]].model_dump()
            provenance, _ = self.require_hand(row["hand_id"])
            feedback = {"recorded_action": recorded, "message": "Your action is legal. The recorded action is descriptive, not an optimal-action label. No action-EV model is promoted.",
                        "source": provenance["source_kind"], "model_version": "legal-baseline-v1"}
            response = {"kind": kind, "to": to, "confidence": confidence,
                        "latency_seconds": (utcnow() - __import__("datetime").datetime.fromisoformat(attempt["started_at"])).total_seconds()}
            conn.execute("UPDATE practice_sessions SET completed_at=?,response=?,feedback=? WHERE attempt_id=?",
                         (utcnow().isoformat(), canonical(response), canonical(feedback), attempt_id))
            due = (utcnow() + timedelta(days=1 if confidence < 3 else 3)).isoformat()
            conn.execute("INSERT INTO study_queue VALUES (?,?,?,1) ON CONFLICT(decision_id) DO UPDATE SET attempts=attempts+1,due_at=excluded.due_at", (attempt["decision_id"], utcnow().isoformat(), due))
            return {**feedback, "response": response, "next_due": due}

    def imports(self):
        with self.connect() as conn: return [json.loads(r[0]) for r in conn.execute("SELECT report FROM ingest_files ORDER BY import_time DESC")]

    def progress(self):
        with self.connect() as conn:
            return {"completed": conn.execute("SELECT COUNT(*) FROM practice_sessions WHERE completed_at IS NOT NULL").fetchone()[0], "bookmarked": conn.execute("SELECT COUNT(*) FROM study_queue").fetchone()[0]}

    def backup(self, destination: Path):
        from .backup import backup
        return backup(self, destination)

    def session_list(self):
        grouped = {}
        for hand in self.iter_hands():
            key = digest([hand['source_kind'],hand['session_id']])[:24]
            session = grouped.setdefault(key, {'session_key':key,'session_id':hand['session_id'],'source_kind':hand['source_kind'],
                                                'hands':0,'eligible':0,'pending':0,'net':0,'hero_hands':0,'started_at':hand['started_at']})
            session['hands'] += 1
            session['eligible'] += int(hand['eligibility']['eligible'])
            session['pending'] += int(not hand['eligibility']['eligible'])
            if hand['net'] is not None:session['net'] += hand['net'];session['hero_hands'] += 1
            if hand['started_at'] and (not session['started_at'] or hand['started_at'] < session['started_at']):session['started_at'] = hand['started_at']
        return list(grouped.values())

    def session_detail(self, session_key, cursor=None):
        from packages.pokermind_core.metrics import summarize
        page = self.hand_page(session_key=session_key, cursor=cursor)
        with self.connect() as conn:
            session = conn.execute('SELECT * FROM sessions WHERE session_key=?', (session_key,)).fetchone()
            rows = conn.execute('SELECT * FROM hands WHERE source_kind=? AND session_id=?', (session['source_kind'], session['session_id']))
            stats = summarize((row, Hand.model_validate_json(row['canonical'])) for row in rows if self.eligibility(row)['eligible'])
        return {**dict(session), 'hands':page['items'], 'next_cursor':page['next_cursor'], 'total':page['total'], 'stats':stats}
