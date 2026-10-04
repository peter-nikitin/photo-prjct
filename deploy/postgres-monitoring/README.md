# Private PostgreSQL monitoring

Repository package only; live role, credential, ingestion cost and notification proof are pending.
The exporter listens on canonical loopback `127.0.0.1:9187`. The agent remotely writes its
metrics from the fixed collector set; database labels include the bounded `datname="app"` label.
Collectors exposing per-user, per-relation and statement dimensions are disabled.
No query text, SQL parameters or customer identifiers are exported.

Before approving a merge that activates this package, the operator must provision
`/opt/photo-prjct/secrets/postgres-exporter-password` as a regular root:nogroup file, mode `0640`,
with a strong printable ASCII credential, and run the reviewed `prepare-role.py` against the
canonical deployment. Its fixed `findme_monitor` identity grants pg_monitor, database CONNECT,
connection limit 2, and no administrative role attributes. Provisioning is separate from automatic
reconciliation. Check the fixed `app` database against canonical configuration before activation.
Approve the measured remote-write series count and current Yandex cost before enabling ingestion.
No VM or cloud operation was performed by the repository implementation.

On main merge, canonical observability fetches the exact approved Git revision, starts only
`postgres-exporter` using the `observability` Compose profile and `--no-deps`, and verifies `pg_up`.
Default product Compose operations exclude this profile. A missing credential or exporter failure
fails observability reconciliation, restores its previous package, and leaves application Deploy
eligible. Monitoring waits for successful same-SHA host jobs and, when Django metrics change,
the successful same-SHA `Deploy` job before waiting for fresh Monium samples and applying rules.

On failure the previous exporter Compose document is restored and only the exporter is restarted;
on first installation only the exporter is removed. Product services and database volumes are
never stopped by this helper. The encompassing host transaction restores agent configuration.
Retain the credential and SQL role while any saved exporter package uses them; removal requires
separate operator approval. Cloud rollback restores the saved dashboard/rules backup through the
existing Monitoring control procedure. Verify private source, agent freshness, Monium evaluator,
and one controlled firing/recovery in email and Telegram before claiming live monitoring complete.
