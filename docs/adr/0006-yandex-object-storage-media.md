# 0006: Use Yandex Object Storage for media

- Status: Accepted
- Date: 2026-07-12
- Deciders: project maintainer
- Supersedes: none
- Superseded by: none

## Context

Event covers must survive container replacement. Later stages also need private originals and public
derivatives. A writable container filesystem cannot provide durable media storage.

## Decision drivers

- Preserve media independently of VM and container lifecycle.
- Keep the deployment on Yandex Cloud and use a Django-compatible S3 API.
- Prevent a public-media policy from exposing private originals.

## Considered options

1. Separate Yandex Object Storage buckets for public and private media.
2. One private bucket with signed access for every object.
3. A persistent volume attached to the application VM.

## Decision

Use Yandex Object Storage through its S3-compatible API. Public covers and derivatives use a public
bucket. Private originals use a separate private bucket introduced with ingestion. PostgreSQL stores
object keys and metadata, not binary content. Credentials and bucket names come from environment
variables.

Object keys are immutable. Replacing a cover creates a new object; automatic deletion is deferred
until media lifecycle rules are specified.

## Consequences

### Positive

- Media survives deployments and VM replacement.
- Separate access boundaries reduce the risk of publishing originals.
- Django can use a standard storage backend.

### Negative

- Buckets, IAM credentials, CORS, lifecycle rules, and cost require operational ownership.
- Replaced covers can leave unreferenced objects until cleanup exists.

### Follow-up

- Provision the public bucket before enabling S3 storage in an environment.
- Define private-media retention and deletion before photo ingestion.

## Validation and rollback

Validate upload and public read in staging while anonymous write and listing remain denied. Roll back
application configuration to filesystem storage only in non-production environments; production
media remains in Object Storage.

## References

- [Architecture: target MVP](../architecture.md#target-mvp-architecture--proposed)
- [MVP roadmap](../plans/2026-07-11-mvp-product-roadmap.md)
