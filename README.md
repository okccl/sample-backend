# sample-backend

Platform Engineering portfolio — サンプルアプリケーション（バックエンド）

## 概要

プラットフォームの各機能が実際にアプリから利用できることを示す、動作確認用サンプル API サーバー。  
FastAPI（Python 3.12）で実装し、**Observability・Stateful・Golden Path** の統合動作を実証する。

アプリ自体の機能（Items CRUD）はシンプルに保ち、**プラットフォームとのインテグレーション**に焦点を当てている。

## このリポジトリが示すもの

| Phase | 内容 |
|-------|------|
| Phase 4 | OpenTelemetry による分散トレーシング（Tempo）と Prometheus メトリクス（`/metrics`）の実装 |
| Phase 5 | `common-app` Library Chart を使い、最小限の `values.yaml` だけでデプロイ定義が完結することを示す |
| Phase 6 | `main` へのpushで自動的にイメージビルド → GHCR プッシュ → `platform-gitops` への PR 作成 → squash merge まで完結する Golden Path |
| Phase 8 | `common-db` Library Chart でプロビジョニングされた PostgreSQL に接続し、Stateful なワークロードとして動作することを示す |
| Phase 11 | PostgreSQL フェイルオーバー時の接続断からアプリが自動回復できることを示す（コネクションプール + リトライ処理） |

## ディレクトリ構成

```
sample-backend/
├── .github/workflows/
│   ├── build.yaml            # CI: イメージビルド & GHCR プッシュ
│   └── update-gitops.yaml    # CD: platform-gitops への image tag 更新通知（PR 方式）
├── src/
│   ├── main.py               # FastAPI アプリケーション本体
│   └── requirements.txt
├── .mise.toml                # ローカル開発ツールバージョン管理
├── .envrc                    # direnv による環境変数設定
└── Dockerfile                # python:3.12-slim ベース
```

> デプロイ定義（Helm values / ArgoCD Application）は `platform-gitops/apps/sample-backend/` で管理。

## API エンドポイント

| Method | Path | 説明 |
|--------|------|------|
| `GET` | `/health` | ヘルスチェック（DB ping 含む） |
| `GET` | `/items` | アイテム一覧取得（PostgreSQL） |
| `POST` | `/items` | アイテム作成（PostgreSQL） |
| `GET` | `/metrics` | Prometheus メトリクス（`ServiceMonitor` 経由で自動収集） |

## Observability 統合

### 分散トレーシング（OpenTelemetry → Tempo）

OTLP exporter を組み込み、DB 操作をスパンとして記録する。`OTEL_EXPORTER_OTLP_ENDPOINT` 環境変数で
エンドポイントを切り替え可能（デフォルト: `http://localhost:4318`）。

```python
# DB 操作をスパンとして計測する例
with tracer.start_as_current_span("db.fetch_items"):
    rows = await conn.fetch("SELECT ...")
```

Grafana の Tempo データソースからトレースを確認できる。

### Prometheus メトリクス

`/metrics` エンドポイントを公開し、`ServiceMonitor` リソースで Prometheus に自動登録される。

```
http_requests_total{method="GET", endpoint="/items"} 42
```

## PostgreSQL 接続の堅牢化（Phase 11）

PostgreSQL フェイルオーバー発生時の接続断から自動回復できるよう、コネクションプールとリトライ処理を実装している。

### コネクションプール

`asyncpg.create_pool()` を使用。コネクションが切断されていた場合でも使用前に検知できる。

| パラメータ | 値 |
|---|---|
| min_size | 2 |
| max_size | 10 |
| max_inactive_connection_lifetime | 30秒 |

### リトライ処理

`tenacity` による指数バックオフ付きリトライを実装。一時的な接続障害のみリトライ対象とし、アプリバグ起因のエラーはリトライしない。

| パラメータ | 値 |
|---|---|
| 最大リトライ回数 | 5回 |
| バックオフ | 指数（1〜16秒） |
| リトライ対象 | `PostgresConnectionError` / `TooManyConnectionsError` / `OSError` |

### /health の DB ping

`/health` エンドポイントで `SELECT 1` による DB 疎通確認を行う。DB 障害時は readinessProbe が失敗してトラフィックが遮断され、フェイルオーバー完了後に自動回復する。

## CI/CD フロー

```
push to main
  └─► build.yaml
        ├─ Docker イメージをビルド（タグ: git SHA short）
        ├─ GHCR (ghcr.io/okccl/sample-backend) にプッシュ
        └─► update-gitops.yaml
              └─► platform-gitops に repository_dispatch を送信
                    └─► gitops/update-sample-backend-{tag} ブランチを作成
                          └─► PR 作成 → squash merge → ブランチ自動削除
                                └─► ArgoCD が新しいイメージタグで自動同期
```

## ローカル開発

```bash
# Python バージョンは mise で管理
mise install

# 依存パッケージのインストール
pip install -r src/requirements.txt

# 起動
uvicorn src.main:app --reload --port 8000
# → http://localhost:8000/docs (Swagger UI)
```

## 関連リポジトリ

| リポジトリ | 役割 |
|---|---|
| [`platform-gitops`](https://github.com/okccl/platform-gitops) | このサービスの Helm values / ArgoCD Application を管理 |
| [`platform-charts`](https://github.com/okccl/platform-charts) | デプロイに使用する `common-app` / `common-db` Library Chart を提供 |
| [`sample-frontend`](https://github.com/okccl/sample-frontend) | CORS 許可対象のフロントエンド（`https://sample-frontend.localhost`） |
