# Docker 单机部署设计

## 目标

为现有单进程 Web 面板提供可复现的单机容器启动方式，保留任务数据库和上传/解析产物，并允许通过环境变量配置宿主端口、监听地址以及已有的 MinerU 和翻译服务。

## 方案

使用一个基于 `python:3.12-slim` 的镜像安装当前项目 `.[web]` 依赖，容器以非 root 用户启动 `python -m pdf_trans.web`。Compose 只编排 Web 服务，不内置 MinerU 或翻译服务，避免引入额外组件；这两个服务继续由 `PDF_TRANS_MINERU_URL`、`TRANSLATION_*` 环境变量接入。

Compose 将容器的 `8000` 端口映射到 `${PDF_TRANS_WEB_PORT:-8000}`，容器内固定监听 `0.0.0.0:8000`。宿主 `./data/web` 挂载到容器 `/app/data/web`，因此 SQLite、上传 PDF、解析结果、日志和 Markdown 在容器重建后仍保留。Compose 的环境变量直接透传，数据库默认继续使用挂载目录中的 SQLite 文件。

## 文件与验证

- `Dockerfile`：构建运行镜像，安装 Web 可选依赖并设置非 root 运行用户。
- `docker-compose.yml`：声明单个 `web` 服务、端口、数据卷、环境变量和重启策略。
- `.dockerignore`：排除本地虚拟环境、Git 元数据、缓存与运行数据。
- `README.md`：记录构建、启动、停止、端口配置、环境变量和数据目录说明。

验证包括 `docker compose config`（若本机安装 Docker Compose）、Docker 镜像构建，以及现有 Python 测试和打包检查。

## 约束

- 不修改 `main`，所有提交保留在 `experiment/web-task-dashboard`。
- 不引入新的 Python 依赖或独立前后端服务。
- 不把密钥写入 Compose 文件；敏感配置通过宿主环境或未提交的 `.env` 提供。
