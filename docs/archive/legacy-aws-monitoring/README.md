# ARCHIVED - never deployed (INFRA-02)

Everything in this folder describes an AWS / CloudWatch / Postgres /
Prometheus / Grafana stack that IMS 2.0 has **never run**. It is kept only as
history. Do not `terraform apply` it, do not copy `prometheus.yml`, and do not
treat it as the monitoring or recovery plan. The resources it names (S3 state
bucket, SNS topics, RDS/Postgres exporter, `recording_rules.yml`, localhost
Alertmanager) are placeholders or missing.

## What is actually deployed and watched

| Piece | Where | How it is watched |
|---|---|---|
| Backend (FastAPI) | Railway | `GET /health` (Dockerfile `HEALTHCHECK`, Railway restart policy) |
| Database | MongoDB on Railway | SENTINEL agent DB probe every tick |
| Cache / bus | Redis on Railway | Event bus falls back to in-process dispatch if Redis is down |
| Frontend | Vercel | SENTINEL frontend reachability probe |

- **SENTINEL** (`backend/agents/implementations/sentinel.py`) scores health
  each tick, stores a snapshot in `health_checks`, and files one
  `alert_history` row per degraded incident (opened / changed / hourly
  reminder); recovery is stamped on the incident row as `recovered_at`.
- **Slack**: `backend/observability.py` `notify_slack` posts HIGH/CRITICAL
  alerts when `SLACK_WEBHOOK_URL` is set; it never raises.
- **Recovery**: Railway redeploy / restart of the service; MongoDB backups are
  Railway's. There is no Terraform-managed infrastructure.
