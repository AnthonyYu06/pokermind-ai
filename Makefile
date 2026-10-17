PY = .venv/bin/python

.PHONY: setup doctor demo test build data-tiny cloud-plan cloud-run
setup:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements.lock
	cd apps/web && npm ci

doctor:
	$(PY) -m services.api.cli doctor

build:
	cd apps/web && npm run build

demo: build
	$(PY) -m services.api.cli seed-demo
	$(PY) -m services.api.cli serve

test:
	$(PY) -m pytest -q
	cd apps/web && npm run build

data-tiny:
	$(PY) -m services.api.cli seed-demo

cloud-plan:
	$(PY) -m services.api.cli cloud-plan

cloud-run:
	$(PY) -m services.api.cli cloud-run $(ARGS)

setup-research:
	$(PY) -m pip install -r requirements-research.lock

pipeline-local:
	$(PY) -m pipelines.spark.local

train-equity:
	$(PY) -m training.equity

evaluate:
	$(PY) -m evaluation.report

batch-score:
	$(PY) -m pipelines.spark.score

research-tiny:
	$(MAKE) pipeline-local
	$(MAKE) train-equity
	$(MAKE) batch-score

GAME ?= kuhn
train-behavior:
	$(PY) -m training.behavior

train-deep-cfr:
	$(PY) -m training.strategy --game $(GAME) --ablations

research-strategy:
	$(MAKE) train-deep-cfr GAME=kuhn
	$(MAKE) train-deep-cfr GAME=leduc
	$(MAKE) train-behavior

pipeline-incremental:
	$(PY) -m pipelines.spark.local --incremental

verify-recovery:
	$(PY) scripts/verify_pipeline_recovery.py

infra-validate:
	.runtime/terraform/terraform -chdir=infra/terraform validate

research-local:
	$(PY) scripts/run_local_research.py

export-local-research:
	$(PY) scripts/export_local_research.py

import-worker:
	$(PY) -m services.api.cli import-worker

release-capture:
	$(PY) -m services.api.cli release-capture

release-list:
	$(PY) -m services.api.cli release-list

cloud-preflight:
	$(PY) scripts/cloud_preflight.py

cloud-wheel:
	$(PY) scripts/build_cloud_wheel.py

verify-cloud-local:
	$(PY) scripts/verify_cloud_workflow.py --data .data/cloud-check-$$(date +%s)

verify-portable-incremental:
	$(PY) scripts/verify_portable_incremental.py --data .data/incremental-check-$$(date +%s)

research-river:
	$(PY) scripts/run_river_research.py

export-river-research:
	$(PY) scripts/export_river_evidence.py

score-river:
	$(PY) -m pipelines.spark.score_river

prepare-coach:
	$(PY) scripts/prepare_river_coach.py

train-holdem-selfplay:
	$(PY) -m training.holdem_nfsp $(ARGS)

test-browser: build
	cd apps/web && PLAYWRIGHT_BROWSERS_PATH=../../.runtime/playwright npx playwright test

container-build:
	$(PY) scripts/build_container.py

verify-container:
	$(PY) scripts/verify_container.py

cloud-inventory:
	$(PY) scripts/cloud_preflight.py $(ARGS)

cloud-status:
	$(PY) -m pipelines.cloud.operations status $(ARGS)

cloud-stop:
	$(PY) -m pipelines.cloud.operations stop $(ARGS)

cloud-reconcile:
	$(PY) -m pipelines.cloud.operations reconcile $(ARGS)

app-stop:
	$(PY) -m pipelines.cloud.operations app-stop $(ARGS)

snapshot-install:
	$(PY) -m services.api.snapshots $(ARGS)

benchmark-cloud:
	$(PY) -m benchmarks.cloud $(ARGS)
