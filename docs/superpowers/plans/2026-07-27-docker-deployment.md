# Docker 单机部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 PDF Trans Web 面板增加轻量的 Dockerfile 和 Docker Compose 单机部署入口，并同步记录使用方式。

**Architecture:** 构建一个安装 `.[web]` 的 Python 3.12 slim 镜像，容器内以非 root 用户运行现有 `pdf_trans.web` 模块。Compose 只启动 Web 服务，把宿主 `data/web` 挂载为持久化数据目录，并通过环境变量连接现有 MinerU 和翻译接口。

**Tech Stack:** Dockerfile、Docker Compose、Python 3.12 slim、项目现有 FastAPI/Uvicorn Web 依赖。

## Global Constraints

- 保持单机、单进程、单任务串行执行。
- 不新增 Python 运行时依赖，不把 MinerU 或翻译服务加入 Compose。
- 容器内监听 `0.0.0.0:8000`，宿主端口由 `PDF_TRANS_WEB_PORT` 配置。
- 运行数据必须持久化到宿主 `./data/web`。
- 所有修改保留在 `experiment/web-task-dashboard`，不合并到 `main`。

### Task 1: Add container build and runtime configuration

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`

**Interfaces:**
- `Dockerfile` produces an image whose default command is `python -m pdf_trans.web` and exposes container port `8000`.
- Compose service `web` consumes `PDF_TRANS_WEB_PORT` (default `8000`) and all `TRANSLATION_*`/`PDF_TRANS_*` variables from the host.

- [ ] **Step 1: Add the Dockerfile**

Use a Python slim base, install the package with its web extra, create an unprivileged user, set `PYTHONUNBUFFERED`, and declare `/app/data/web` as the persistent data location:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDF_TRANS_WEB_HOST=0.0.0.0 \
    PDF_TRANS_WEB_PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir '.[web]' \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/web \
    && chown -R appuser:appuser /app

USER appuser

VOLUME ["/app/data/web"]
EXPOSE 8000

CMD ["python", "-m", "pdf_trans.web"]
```

- [ ] **Step 2: Add Compose service**

Define one service with a configurable host port, persistent bind mount, restart policy, and explicit environment passthrough. Keep the database URL default aligned with the mounted path:

```yaml
services:
  web:
    build:
      context: .
    image: pdf-trans-web:local
    ports:
      - "${PDF_TRANS_WEB_PORT:-8000}:8000"
    environment:
      PDF_TRANS_WEB_HOST: 0.0.0.0
      PDF_TRANS_WEB_PORT: 8000
      PDF_TRANS_WEB_DATA_DIR: /app/data/web
      PDF_TRANS_DATABASE_URL: ${PDF_TRANS_DATABASE_URL:-sqlite:////app/data/web/pdf_trans.db}
      PDF_TRANS_MAX_UPLOAD_MIB: ${PDF_TRANS_MAX_UPLOAD_MIB:-200}
      PDF_TRANS_MINERU_URL: ${PDF_TRANS_MINERU_URL:-http://host.docker.internal:7100}
      TRANSLATION_BASE_URL: ${TRANSLATION_BASE_URL:?set TRANSLATION_BASE_URL}
      TRANSLATION_API_KEY: ${TRANSLATION_API_KEY:?set TRANSLATION_API_KEY}
      TRANSLATION_MODEL: ${TRANSLATION_MODEL:?set TRANSLATION_MODEL}
      TRANSLATION_TIMEOUT_SECONDS: ${TRANSLATION_TIMEOUT_SECONDS:-120}
      TRANSLATION_MAX_RETRIES: ${TRANSLATION_MAX_RETRIES:-1}
      TRANSLATION_CONCURRENCY: ${TRANSLATION_CONCURRENCY:-5}
    volumes:
      - ./data/web:/app/data/web
    restart: unless-stopped
```

- [ ] **Step 3: Add build exclusions**

Exclude `.git`, `.venv`, caches, local test/build output, and `data/web` so credentials and runtime artifacts are never copied into the image:

```text
.git
.gitignore
.venv
__pycache__
*.py[cod]
.pytest_cache
.mypy_cache
*.egg-info
build
dist
data/web
```

- [ ] **Step 4: Validate configuration**

Run `docker compose config` with placeholder required variables and confirm it exits zero; if Docker is unavailable, parse the YAML with the installed Compose/Docker tool or report that limitation explicitly.

- [ ] **Step 5: Commit container files**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "feat: add docker deployment files"
```

### Task 2: Document container usage

**Files:**
- Modify: `README.md` after the Web 面板使用 section

- [ ] **Step 1: Add build and start commands**

Document preparing a `.env` from the existing translation variables, then running:

```bash
docker compose up --build
```

Include `docker compose down`, the browser URL, the `PDF_TRANS_WEB_PORT` override, and the fact that `./data/web` is persistent.

- [ ] **Step 2: Verify README commands match Compose**

Check that every variable and path documented in the example exists in `docker-compose.yml`, and that the default URL/port agrees with `WebSettings`.

- [ ] **Step 3: Commit documentation**

```bash
git add README.md
git commit -m "docs: document docker deployment"
```

### Task 3: Run regression verification

**Files:**
- Test: existing `tests/`

- [ ] **Step 1: Run Python test suite**

Run `python3 -m pytest -v`; expected result is zero failures.

- [ ] **Step 2: Run package build**

Run `python3 -m build`; expected result is exit code 0.

- [ ] **Step 3: Inspect git state and branch**

Run `git status --short --branch` and `git log --oneline -3`; verify the branch is `experiment/web-task-dashboard`, only intended files changed, and `main` was not modified.
