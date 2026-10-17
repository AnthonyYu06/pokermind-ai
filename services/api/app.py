from contextlib import asynccontextmanager
from typing import Literal
from pathlib import Path
from urllib.parse import urlparse
import json
import os

from fastapi import FastAPI, Request, UploadFile, Query
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from packages.ignition_import.parser import MAX_BYTES
from packages.pokermind_core.contracts import Contract, canonical, Hand
from packages.pokermind_core.eligibility import utcnow
from packages.pokermind_core.equity import score_observation
from packages.pokermind_core.metrics import summarize
from .store import Store
from .security import BodyLimitMiddleware

ROOT = Path(__file__).resolve().parents[2]


class ReleaseChoice(Contract):
    release_id: str = Field(min_length=64,max_length=64)


class RangeRequest(Contract):
    range_preset: Literal['uniform','pair_heavy','broadway_heavy'] = 'uniform'
    samples: int = Field(default=1000, strict=True, ge=100, le=1000)


class HeroChoice(Contract):
    seat: int = Field(ge=1, le=9)


class RetentionChoice(Contract):
    days: int | None = Field(default=30, strict=True, ge=1, le=3650)


class DeletionPreview(Contract):
    scope: Literal["all_real", "expired_previews"]


class DeletionChoice(DeletionPreview):
    token: str = Field(min_length=64, max_length=64)
    confirmation: Literal["DELETE"]


class SimulationStart(Contract):
    game: Literal["kuhn", "leduc"] = "kuhn"
    opponent: Literal["baseline", "research"] = "baseline"


class SimulationAction(Contract):
    action: int = Field(strict=True, ge=0, le=2)
    revision: int = Field(strict=True, ge=0)


class HoldemStart(Contract):
    opponent: Literal["baseline", "research", "selfplay"] = "baseline"


class PolicyRevision(Contract):
    revision: int = Field(strict=True, ge=0)


class CoachStart(Contract):
    pass


class CoachChoice(Contract):
    action: Literal['fold', 'check', 'call', 'half_pot', 'pot', 'double_pot', 'all_in'] | None = None


class HoldemAction(Contract):
    kind: Literal["fold", "check", "call", "bet", "raise"]
    to: int | None = Field(default=None, strict=True, ge=0)
    revision: int = Field(strict=True, ge=0)


class Attempt(Contract):
    kind: str
    to: int | None = Field(default=None, strict=True, ge=0)
    confidence: int = Field(default=3, ge=1, le=5)


def create_app(data_dir: Path | None = None):
    @asynccontextmanager
    async def lifespan(app):
        jobs.start()
        try: yield
        finally: jobs.close()
    app = FastAPI(title="PokerMind • offline study", docs_url=None, redoc_url=None, lifespan=lifespan)
    store = Store(data_dir or Path(os.environ.get("POKERMIND_DATA", ROOT / ".data")))
    app.state.store = store
    from .import_jobs import ImportJobs
    jobs = ImportJobs(store)
    app.state.import_jobs = jobs
    from .small_games import SmallGames
    games = SmallGames(store)
    from .holdem import HoldemGames
    holdem = HoldemGames(store)
    from .coach import RiverCoach
    coach = RiverCoach(store)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get("x-pokermind-local") != "1" or request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlparse(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "Same-origin local requests only"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def bad_value(request, exc): return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc): return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(KeyError)
    async def missing(request, exc): return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/api/health")
    def health(): return {"status": "ok", "mode": "offline-local", "cloud_enabled": False, "version": "0.1.0"}

    @app.get("/api/overview")
    def overview():
        return store.overview()

    @app.get("/api/hands")
    def hands(source: Literal["real", "synthetic"] | None = None, query: str = Query(default="", max_length=120),
              cursor: int | None = Query(default=None, ge=1), limit: int = Query(default=50, ge=1, le=100)):
        return store.hand_page(source, query, cursor, limit)

    @app.get("/api/sessions")
    def sessions():
        from .browse import session_page
        return session_page(store,'real')['items']+session_page(store,'synthetic')['items']

    @app.get("/api/sessions/{session_key}")
    def session(session_key: str, cursor: int | None = Query(default=None, ge=1)): return store.session_detail(session_key, cursor)

    @app.get("/api/imports")
    def imports():
        from .browse import import_page
        return import_page(store)['items']

    @app.get('/api/import-pages')
    def import_pages(cursor: int | None = Query(default=None, ge=1), limit: int = Query(default=30, ge=1, le=100), query: str = Query(default='', max_length=120)):
        from .browse import import_page
        return import_page(store,cursor,limit,query)

    @app.get('/api/imports/{file_id}')
    def import_report(file_id: str, after: int = Query(default=0,ge=0), limit: int = Query(default=100,ge=1,le=200), status: Literal['ready','imported','duplicate','unsupported','quarantined','failed','waiting'] | None = None):
        from .browse import import_detail
        return import_detail(store,file_id,after,limit,status)

    @app.get('/api/session-pages')
    def session_pages(source: Literal['real','synthetic'] = 'synthetic', cursor: int | None = Query(default=None,ge=1), limit: int = Query(default=30,ge=1,le=100), query: str = Query(default='',max_length=120)):
        from .browse import session_page
        return session_page(store,source,cursor,limit,query)

    @app.get('/api/import-jobs')
    def import_jobs(): return jobs.list()

    @app.post('/api/import-jobs', status_code=202)
    async def submit_import(file: UploadFile):
        if Path(file.filename or '').suffix.lower() not in ('.txt','.jsonl'):
            raise ValueError('Choose a static .txt or .jsonl file; archives are unsupported')
        data=await file.read(MAX_BYTES+1);await file.close()
        return jobs.submit(data,file.filename or 'export.txt')

    @app.post('/api/imports/{file_id}/commit-job', status_code=202)
    def commit_job(file_id: str): return jobs.commit(file_id)

    @app.get('/api/import-jobs/{job_id}')
    def import_job(job_id: str): return jobs.get(job_id)

    @app.post('/api/import-jobs/{job_id}/cancel')
    def cancel_job(job_id: str): return jobs.cancel(job_id)

    @app.post('/api/import-jobs/{job_id}/retry', status_code=202)
    def retry_job(job_id: str): return jobs.retry(job_id)

    @app.get("/api/data")
    def data_inventory():
        from .privacy import inventory
        return inventory(store)

    @app.post("/api/data/retention")
    def retention(choice: RetentionChoice):
        from .privacy import configure
        return configure(store, choice.days)

    @app.post("/api/data/delete-preview")
    def delete_preview(choice: DeletionPreview):
        from .privacy import preview
        return preview(store, choice.scope)

    @app.post("/api/data/delete")
    def delete_data(choice: DeletionChoice):
        from .privacy import delete
        return delete(store, choice.scope, choice.token, choice.confirmation)

    @app.post("/api/data/retry-cleanup")
    def retry_cleanup():
        from .privacy import cleanup
        return {"pending_file_deletions": cleanup(store)}

    @app.get("/api/imports/{file_id}/original")
    def original(file_id: str):
        from .privacy import original
        checksum, content = original(store, file_id)
        return Response(content, media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="pokermind-original-{checksum[:12]}.txt"',
            "X-Content-SHA256": checksum})

    @app.post("/api/imports/preview")
    async def preview(file: UploadFile):
        # Read at most the advertised bound + 1; archives never unpacked.
        if Path(file.filename or "").suffix.lower() not in (".txt", ".jsonl"):
            raise ValueError("Choose a static .txt or .jsonl file; archives are unsupported")
        data = await file.read(MAX_BYTES + 1)
        await file.close()
        if len(data) > MAX_BYTES: return JSONResponse({"detail": "File exceeds 10 MiB"}, status_code=413)
        return store.preview(data, file.filename or "export.txt")

    @app.post("/api/imports/{file_id}/commit")
    def commit(file_id: str): return store.commit(file_id)

    @app.post("/api/imports/{file_id}/cancel")
    def cancel(file_id: str): return store.cancel(file_id)

    @app.post("/api/demo")
    def demo(): return store.seed_demo()

    @app.get("/api/hands/{hand_id}/replay")
    def replay(hand_id: str, retrospective: bool = False): return store.replay(hand_id, retrospective)

    @app.post("/api/hands/{hand_id}/declare-complete")
    def declare(hand_id: str): return store.declare(hand_id)

    @app.post("/api/hands/{hand_id}/hero")
    def hero(hand_id: str, choice: HeroChoice): return store.select_hero(hand_id, choice.seat)

    @app.post("/api/decisions/{decision_id}/bookmark")
    def bookmark(decision_id: str): return store.bookmark(decision_id)

    @app.get("/api/decisions/{decision_id}/analysis")
    def analysis(decision_id: str):
        store.decision(decision_id)
        with store.connect() as conn: row = conn.execute("SELECT result FROM analysis_results WHERE decision_id=?", (decision_id,)).fetchone()
        return json.loads(row[0]) if row else {"status": "unavailable", "reason": "No reference result has been computed for this decision"}

    @app.post("/api/decisions/{decision_id}/analyze")
    def analyze(decision_id: str):
        _, hand, obs = store.decision(decision_id)
        with store.connect() as conn:
            cached = conn.execute("SELECT result FROM analysis_results WHERE decision_id=?", (decision_id,)).fetchone()
        if cached:
            cached_result = json.loads(cached[0])
            if cached_result.get("observation_hash") == obs["observation_hash"]:
                return cached_result
        result = score_observation(obs, seed=int(obs["observation_hash"][:8], 16))
        result.update(decision_id=decision_id, observation_hash=obs["observation_hash"], data_hash=__import__("hashlib").sha256(canonical(hand).encode()).hexdigest())
        with store.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO analysis_results VALUES (?,?,?,?,?)", (decision_id, result["model_version"], result["data_hash"], utcnow().isoformat(), canonical(result)))
        return result

    @app.post('/api/decisions/{decision_id}/range-analysis')
    def range_analysis(decision_id: str, choice: RangeRequest):
        from .analysis import analyze
        return analyze(store,decision_id,'range',choice.range_preset,choice.samples)

    @app.post('/api/decisions/{decision_id}/rollout')
    def rollout_analysis(decision_id: str, choice: RangeRequest):
        from .analysis import analyze
        return analyze(store,decision_id,'rollout',choice.range_preset,choice.samples)

    @app.get("/api/study")
    def study(): return {"queue": store.queue(), "progress": store.progress()}

    @app.post("/api/practice")
    def practice(): return store.start_practice()

    @app.post("/api/practice/{attempt_id}")
    def submit(attempt_id: str, attempt: Attempt):
        return store.submit_practice(attempt_id, attempt.kind, attempt.to, attempt.confidence)

    @app.post("/api/holdem")
    def holdem_start(choice: HoldemStart): return holdem.start(choice.opponent)

    @app.post('/api/coach')
    def coach_start(choice: CoachStart): return coach.start()

    @app.get('/api/coach/{drill_id}')
    def coach_get(drill_id: str): return coach.get(drill_id)

    @app.post('/api/coach/{drill_id}/analyze')
    def coach_analyze(drill_id: str, choice: CoachChoice): return coach.compare(drill_id, choice.action)

    @app.get("/api/holdem/{game_id}")
    def holdem_get(game_id: str): return holdem.get(game_id)

    @app.post("/api/holdem/{game_id}/action")
    def holdem_act(game_id: str, choice: HoldemAction): return holdem.act(game_id, choice.kind, choice.to, choice.revision)

    @app.post('/api/holdem/{game_id}/policy')
    def holdem_policy(game_id: str, choice: PolicyRevision): return holdem.policy(game_id, choice.revision)

    @app.post('/api/holdem/{game_id}/bot-action')
    def holdem_bot_action(game_id: str, choice: PolicyRevision): return holdem.bot_action(game_id, choice.revision)

    @app.get('/api/research/holdem-nfsp')
    def holdem_research():
        from .nfsp_policy import research
        value=research(store.root)
        if value['status']=='not-run':return value
        import importlib.util
        try:available=importlib.util.find_spec('torch') is not None
        except ImportError:available=False
        return {**{k:value[k] for k in ('version','status','quality_gates','promotion','runs','selection','cloud_enabled')},
                'runtime_available':available}

    @app.get("/api/simulator/options")
    def simulator_options(): return games.options()

    @app.post("/api/simulator")
    def simulator_start(choice: SimulationStart): return games.start(choice.game, choice.opponent)

    @app.get("/api/simulator/{game_id}")
    def simulator_get(game_id: str): return games.get(game_id)

    @app.post("/api/simulator/{game_id}/action")
    def simulator_act(game_id: str, choice: SimulationAction): return games.act(game_id, choice.action, choice.revision)

    @app.get("/api/research/strategy")
    def strategy_research():
        from .artifacts import report
        results = []
        for game in ["kuhn", "leduc"]:
            if (store.root / f"strategy-{game}.json").exists():
                r = report(store.root, "strategy-" + game)
                results.append({k:r[k] for k in ("game", "status", "baselines", "independent_reference", "mean_exploitability", "std_exploitability", "promotion")})
        return results

    @app.get("/api/research/behavior")
    def behavior_research():
        from .artifacts import report
        if not (store.root / "behavior.json").exists(): return {"status":"not-run"}
        r = report(store.root, "behavior")
        return {"status":r["status"], "domain":r["domain"], "promotion":r["promotion"], "dataset":r["dataset"]["decisions"],
                "frequency":r["baselines"]["frequency"], "runs":[{k:run[k] for k in ("model","seed","metrics")} for run in r["runs"]]}

    @app.get('/api/releases')
    def releases():
        from .releases import inventory
        return inventory(store.root)

    @app.post('/api/releases/capture')
    def capture_release():
        from .releases import capture
        return capture(store.root)

    @app.post('/api/releases/activate')
    def activate_release(choice: ReleaseChoice):
        from .releases import activate
        return activate(store.root,choice.release_id)

    @app.get("/api/project")
    def project():
        return json.loads((ROOT / "configs" / "milestones.json").read_text())

    @app.get("/api/research")
    def research():
        pointer = store.root / "research.json"
        if not pointer.exists(): return {"status": "not-run"}
        from .artifacts import report as read_report
        report = read_report(store.root,'research')
        return {k:report[k] for k in ("status","dataset_id","domain","promotion","counts","baselines","mean_test_mae","std_test_mae","model","device")}

    @app.get('/api/research/local')
    def local_research():
        from .artifacts import verified_bytes
        pointer=store.root/'local-research.json'
        if not pointer.exists():return {'status':'not-run'}
        reference=json.loads(pointer.read_text())
        return json.loads(verified_bytes(store.root,reference['path'],reference['sha256']))

    @app.get('/api/research/river')
    def river_research():
        from .artifacts import verified_bytes
        pointer = store.root / 'river-research.json'
        if not pointer.exists(): return {'status': 'not-run'}
        reference = json.loads(pointer.read_text())
        return json.loads(verified_bytes(store.root, reference['path'], reference['sha256']))

    dist = ROOT / "apps" / "web" / "dist"
    if (dist / 'index.html').is_file() and (dist / 'assets').is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets",check_dir=False), name="assets")

        @app.get("/")
        def index(): return FileResponse(dist / "index.html")
    app.add_middleware(BodyLimitMiddleware)
    return app
