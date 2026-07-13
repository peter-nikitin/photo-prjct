# Architecture Decision Records

ADRs capture durable decisions that materially affect system structure, operations, security, data,
or development workflow. They explain why a choice was made; `architecture.md` describes the
resulting system.

## Lifecycle

- **Proposed**: under discussion; implementation must not rely on it as settled.
- **Accepted**: approved and authoritative.
- **Rejected**: considered but not selected.
- **Superseded**: replaced by a later ADR, linked in both records.

Accepted ADRs are immutable except for spelling, formatting, and link corrections. Change a decision
with a new ADR that supersedes the old one. Allocate the next four-digit number and use a lowercase
hyphenated filename: `NNNN-short-title.md`. Copy [the template](0000-template.md), never edit it in
place, and add the new record to this index.

## Index

| Number | Decision | Status |
| --- | --- | --- |
| 0001 | [Use a Django modular monolith](0001-django-modular-monolith.md) | Accepted |
| 0002 | [Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md) | Accepted |
| 0003 | [Deploy with Docker Compose to Yandex Cloud](0003-docker-compose-yandex-cloud.md) | Accepted |
| 0004 | [Keep engineering knowledge in the repository](0004-repository-engineering-knowledge.md) | Accepted |
| 0005 | [Promote immutable images through staging](0005-promote-images-through-staging.md) | Accepted |
| 0006 | [Use Yandex Object Storage for media](0006-yandex-object-storage-media.md) | Accepted |
| 0007 | [Use Nginx and Certbot for the HTTPS edge](0007-nginx-certbot-https-edge.md) | Accepted |
