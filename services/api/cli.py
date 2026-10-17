import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from packages.pokermind_core.contracts import canonical
from .store import Store

ROOT = Path(__file__).resolve().parents[2]


def doctor():
    versions = {}
    for package in ("fastapi", "pydantic", "treys", "torch", "pyspark", "delta-spark", "mlflow", "open-spiel"):
        try: versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package] = "not installed"
    try:
        java = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[0]
    except (FileNotFoundError, IndexError): java = "missing"
    result = {"python": sys.version.split()[0], "machine": platform.machine(), "platform": platform.platform(),
              "versions": versions, "java": java, "cloud_enabled": False,
              "data_note": "Spark 4 requires Java 17+; set JAVA_HOME to an ARM64 JDK 17 before running pipeline-local",
              "cuda": "not tested", "mps": "optional; CPU reference is default"}
    print(json.dumps(result, indent=2))
    return 0 if sys.version_info[:2] == (3, 13) and all(versions[p] != "not installed" for p in ("fastapi", "pydantic", "treys")) else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="PokerMind — offline study of completed sessions")
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("POKERMIND_DATA", ROOT / ".data")))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("seed-demo")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    imp = commands.add_parser("import-preview")
    imp.add_argument("file", type=Path)
    commit = commands.add_parser("import-commit")
    commit.add_argument("file_id")
    replay = commands.add_parser("replay")
    replay.add_argument("hand_id")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    commands.add_parser("data-status")
    commands.add_parser("import-jobs")
    queued = commands.add_parser("import-queue")
    queued.add_argument("file",type=Path)
    queued_commit=commands.add_parser("import-commit-queue")
    queued_commit.add_argument("file_id")
    for name in ("import-job-cancel","import-job-retry"):
        command=commands.add_parser(name);command.add_argument("job_id")
    commands.add_parser("import-worker")
    commands.add_parser("release-capture")
    commands.add_parser("release-list")
    release=commands.add_parser("release-activate");release.add_argument("release_id")
    check=commands.add_parser("release-check");check.add_argument("role",choices=("research","behavior","strategy-kuhn","strategy-leduc"))
    retention = commands.add_parser("data-retention")
    retention.add_argument("days", help="1–3650 days for abandoned previews, or disabled")
    purge = commands.add_parser("data-delete-preview")
    purge.add_argument("scope", choices=("all_real", "expired_previews"))
    deletion = commands.add_parser("data-delete")
    deletion.add_argument("scope", choices=("all_real", "expired_previews"))
    deletion.add_argument("--token", required=True, help="Token from a reviewed deletion preview")
    deletion.add_argument("--confirm", required=True, choices=("DELETE",))
    export = commands.add_parser("export-original")
    export.add_argument("file_id")
    export.add_argument("destination", type=Path)
    commands.add_parser("cloud-plan")
    cloud = commands.add_parser("cloud-run")
    cloud.add_argument('--plan', type=Path); cloud.add_argument('--token')
    cloud.add_argument('--evidence', type=Path, default=Path('.data/cloud-execution'))
    args = parser.parse_args()
    try:
        if args.command == "doctor": return doctor()
        if args.command == "cloud-plan":
            print((ROOT / "docs" / "cloud-plan.md").read_text())
            return 0
        if args.command == "cloud-run":
            policy_path = ROOT / 'configs/cost-policy.json'
            if not json.loads(policy_path.read_text()).get('cloud_enabled'):
                raise ValueError("Cloud execution is disabled. No budget or account is approved. Review docs/cloud-plan.md.")
            if not args.plan or not args.token: raise ValueError('Supply --plan and --token for the exact approved run')
            from pipelines.cloud.launch import main as launch
            launch(['--plan', str(args.plan), '--token', args.token, '--policy', str(policy_path), '--evidence', str(args.evidence)])
            return 0
        if args.command == "restore":
            from .backup import restore
            print(json.dumps(restore(args.source, args.destination), indent=2))
            return 0
        store = Store(args.data)
        if args.command == "seed-demo": print(json.dumps(store.seed_demo(), indent=2))
        elif args.command == "import-preview":
            from packages.ignition_import.parser import MAX_BYTES
            with args.file.open("rb") as f: print(json.dumps(store.preview(f.read(MAX_BYTES+1), args.file.name), indent=2))
        elif args.command == "import-commit": print(json.dumps(store.commit(args.file_id), indent=2))
        elif args.command == "replay": print(canonical(store.replay(args.hand_id)))
        elif args.command == "backup": print(json.dumps(store.backup(args.destination), indent=2))
        elif args.command.startswith("release-"):
            from .releases import capture,inventory,activate,promotion_check
            result=capture(store.root) if args.command=="release-capture" else inventory(store.root) if args.command=="release-list" else activate(store.root,args.release_id) if args.command=="release-activate" else promotion_check(store.root,args.role)
            print(json.dumps(result,indent=2))
        elif args.command in ("import-jobs","import-queue","import-commit-queue","import-job-cancel","import-job-retry","import-worker"):
            from .import_jobs import ImportJobs
            jobs=ImportJobs(store)
            if args.command=="import-jobs":result=jobs.list()
            elif args.command=="import-commit-queue":result=jobs.commit(args.file_id)
            elif args.command=="import-queue":
                from packages.ignition_import.parser import MAX_BYTES
                if args.file.suffix.lower() not in (".txt",".jsonl"):raise ValueError("Choose a .txt or .jsonl export")
                with args.file.open("rb") as source:result=jobs.submit(source.read(MAX_BYTES+1),args.file.name)
            elif args.command=="import-job-cancel":result=jobs.cancel(args.job_id)
            elif args.command=="import-job-retry":result=jobs.retry(args.job_id)
            else:
                import time
                initial={j['job_id'] for j in jobs.list() if j['status'] in ('queued','running')}
                jobs.start()
                if not jobs.thread:raise ValueError("Another process owns the import worker; use import-jobs for progress")
                try:
                    while any(j["status"] in ("queued","running") for j in jobs.list()):time.sleep(.1)
                finally:jobs.close()
                result=jobs.list()
            print(json.dumps(result,indent=2))
            if args.command=='import-worker' and any(j['job_id'] in initial and j['status'] in ('failed','interrupted') for j in result):return 2
        elif args.command == "data-status":
            from .privacy import inventory
            print(json.dumps(inventory(store), indent=2))
        elif args.command == "data-retention":
            from .privacy import configure
            print(json.dumps(configure(store, None if args.days == "disabled" else int(args.days)), indent=2))
        elif args.command == "data-delete-preview":
            from .privacy import preview
            print(json.dumps(preview(store, args.scope), indent=2))
        elif args.command == "data-delete":
            from .privacy import delete
            print(json.dumps(delete(store, args.scope, args.token, args.confirm), indent=2))
        elif args.command == "export-original":
            from .privacy import original
            checksum, content = original(store, args.file_id)
            with args.destination.open("xb") as out:
                out.write(content)
            print(json.dumps({"status": "exported", "sha256": checksum, "destination": str(args.destination)}))
        elif args.command == "serve":
            import uvicorn
            from .app import create_app
            uvicorn.run(create_app(args.data), host="127.0.0.1", port=args.port)
        return 0
    except (ValueError, KeyError, PermissionError, OSError) as exc:
        print(f"PokerMind: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
