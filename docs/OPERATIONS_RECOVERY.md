# Operations recovery contract

## Targets

- **RPO:** 60 minutes by default. Run `ops/backup/backup_services.py` at least hourly and ship the bundle off-host.
- **RTO:** 120 minutes by default. The rehearsal script measures bundle validation time; a production rehearsal must additionally restore PostgreSQL, Redis, Qdrant, and attachments into an isolated namespace.

## Backup scope

1. PostgreSQL custom-format dump, including task/event/run/artifact metadata.
2. Redis RDB for checkpoints and transient coordination state.
3. Qdrant storage snapshot or export.
4. Attachment/object-storage bytes and manifests.
5. Connector encryption-key fingerprint. The secret itself is never written to the manifest, but restore cannot decrypt connector credentials without the same key.

## Restore order

1. Provision an isolated database and restore PostgreSQL.
2. Restore the connector encryption key from the secret manager.
3. Restore Redis and Qdrant.
4. Restore object storage/attachments.
5. Run migrations and readiness checks.
6. Run `scripts/restore_rehearsal.py` and the smoke/release gates.
7. Verify task replay, approval inbox, artifact export, and queue worker claim/retry behavior.

## Alerts

Load `ops/observability/alerts.yml` into Prometheus. Alerts are deliberately based on operator-facing SLOs: queue age, worker presence, provider failure rate, approval age, and backup freshness.