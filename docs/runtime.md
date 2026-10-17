# Runtime matrix

Actual development host: macOS 26.5, ARM64, Python 3.13.5, Node 26.7.0. The base
application does not require Java, Spark, MLflow, PyTorch, Docker or cloud accounts.

| Layer | Selected / tested |
|---|---|
| Python | 3.13.5 (project accepts 3.13.x) |
| Node | 26.7.0, `.nvmrc`; npm lockfile |
| API | Exact FastAPI/Starlette/Pydantic versions in `requirements.lock` |
| React / TypeScript / Vite | 19.1.0 / 5.8.3 / 6.4.4 |
| Java | Temurin ARM64 17.0.20.1+1, project-local `.runtime` |
| PySpark / Delta | 4.0.1 / 4.0.0; real local writes tested |
| PyTorch / NumPy | 2.13.0 / 2.3.1; CPU training and safe reload tested |
| MLflow | 3.17.1; local SQLite tracking and actual runs |
| MPS / CUDA / Databricks Runtime | Forward/optimizer checks passed / unavailable / unverified |

[Spark 4.0.1 installation](https://spark.apache.org/docs/4.0.1/api/python/getting_started/install.html)
requires Java 17+ and supports Python 3.9+.
[Delta compatibility](https://docs.delta.io/releases/) pairs Delta 4.0.x with Spark
4.0.x. [PyTorch installation](https://pytorch.org/get-started/locally/) documents
macOS and supported Python versions. These sources were rechecked October 15,
2026; the actual end-to-end local run is stronger evidence for this exact matrix.

System Java was 11/x86_64 and remains unchanged. A project-local ARM64 JDK was
downloaded from the official [Adoptium API](https://adoptium.net/installation).
The pipeline discovers `.runtime/jdk-17*/Contents/Home`, or uses explicit JAVA_HOME.
For another Mac, install an ARM64 JDK 17 and set JAVA_HOME before running the data
workflow. Nothing installs a global daemon or changes shell startup files.

Base dependencies and research dependencies are separately locked. Research
installation is a large download (Spark alone ~434 MB compressed). Maven downloads
the matching Delta jars on first launch. Local ports must be allowed by the OS or
sandbox for Spark's JVM/Python workers. This is local networking, not cloud compute.

Research execution is native macOS. The Linux/amd64 serving container was also
built and tested under local Docker emulation with a read-only root, nonroot user
and external networking disabled. Linux CI is defined but has not run remotely;
signed desktop packaging and cloud hardware remain unverified.

The first exploratory run used PyTorch 2.10.0 and MLflow 3.10.1. A dependency audit
identified patched releases, so the final lock uses PyTorch 2.13.0, MLflow 3.17.1
and cryptography 50.0.0 and the same experiment was rerun. MLflow 3.15.0's crypto
upper bound was incompatible; dependency resolution selected 3.17.1. Original
results are retained separately, not silently attributed to the patched runtime.

## Added local research/runtime checks

OpenSpiel 2.0.2 has an ARM64 CPython 3.13 wheel and was installed and executed
locally. The independent C++ CFR and Python exact best-response references passed
against the project's implementations for Kuhn and Leduc. The research dependency
lock was regenerated and pip-audit reported no known advisories.

Terraform 1.16.6 is installed only under ignored `.runtime/terraform`, with the
official archive checksum verified. Its signed AWS provider 6.69.1 schema validated
locally. The provider requires a local subprocess socket; sandboxed validation may
need that permission. No AWS credentials, remote backend, plan or apply was used.

Databricks 17.3 LTS supplies Python 3.12.3 / Spark 4.0.0 / Delta 4.0.0 / Java 17.
The main app requires Python 3.13; the separate batch wheel accepts Python 3.12.
See the compatibility evidence below and the cloud plan before packaging a job.

The larger local profile used the same CPU runtime on a 12-logical-CPU / 18 GiB
Mac. MPS forward parity and finite gradient/optimizer steps passed for the equity
MLP, card set and behavior GRU; this does not claim MPS quality or speed. CUDA was
unavailable. `scripts/check_base_runtime.py` also exercises the base suite with
research imports deliberately unavailable. It checks dependency separation on
this host, not installation on Linux. See `benchmarks/results/device-compatibility.json`.

## Separately tested batch compatibility build

The original Python mismatch is now addressed by a separate `pokermind-batch`
wheel (`>=3.12,<3.14`), without changing the main app's Python 3.13 contract.
A fresh local Python 3.12.15 environment ran the complete portable workflow with
Spark 4.0.0, Delta 4.0.0, Torch 2.7.0, NumPy 2.1.3, PyArrow 18.0.0 and MLflow 3.0.1.
It restored 256 hands / 338 scores, tested checkpoint resume, registered a model in
the local MLflow registry, and passed SDK contract tests with boto3 1.36.2 and
Databricks SDK 0.49.0. The installed wheel was imported outside the repository.
`pip check` reported no broken requirements. This is macOS CPU compatibility
validation, not the exact managed Databricks image or an executed cloud run.

### Dependency audit and patched local harness

Auditing the original compatibility environment reported 86 advisory entries
across seven upstream packages (some entries share aliases). That environment
was used only for local synthetic tests and never exposed an MLflow server.
The report is retained as `benchmarks/results/python312-compatibility-audit.json`;
the successful compatibility run does not establish deployment security.

The local harness was then upgraded to Torch 2.13.0, MLflow 3.17.1, PyArrow 23.0.1,
Flask 3.1.3, setuptools 84.0.0, wheel 0.46.3 and pip 26.2. Its third-party audit
now reports zero known vulnerabilities. The local `pokermind-batch` wheel is
skipped by PyPI advisory lookup and is covered by source/contract tests instead.
`requirements-cloud-local.lock` pins this Python 3.12/macOS harness; it is **not**
a Databricks installation file. Spark remains 4.0.0 and Delta remains 4.0.0.

The actual managed runtime needs its own current patch review before spending is
enabled. Upstream version findings do not prove whether a vendor has backported a
fix. Consult the [Databricks maintenance policy](https://docs.databricks.com/aws/en/machine-learning/databricks-runtime-ml-maintenance)
and validate the exact workspace image rather than overwriting its managed Spark
environment with this local lock.

The patched harness passes 95 tests (two OpenSpiel-dependent modules skipped),
all 20 targeted cloud/river tests, and the four-stage registry/backup workflow
(256 restored hands, 338 restored scores). Its data output identities match the
original Python 3.12 and Python 3.13 runs. Updated build-tool pins and the wheel
builder itself participate in the content version; installation/import outside
the checkout was verified again.
