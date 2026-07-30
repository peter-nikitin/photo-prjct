# Revisit bearer-response log suppression on Django upgrade

## Observed gap

The public selfie-result middleware uses Django's internal response attribute
`_has_been_logged` to stop `BaseHandler` from emitting a second 4xx/5xx request log after the
bearer path has been redacted or an unexpected error has been converted to a generic response.
The current Django 6.0.6 implementation honors that attribute through `django.utils.log.log_response`,
but it is not a documented application-level API.

## Why this does not block the current task

Focused regressions cover CSRF-enforced non-read requests, a forced unexpected 500, and normal
bearer 404s on the installed Django version. They prove the current production path retains the
required privacy headers without writing the plaintext bearer token to Django request logs.

## Revisit trigger

Before upgrading Django beyond 6.0.x, or when a dependency update changes `BaseHandler` or
`django.utils.log.log_response`, rerun the bearer privacy regressions and inspect the replacement
logging contract. Replace the internal flag with a supported API if Django supplies one; otherwise
update the middleware and tests against the new behavior before deploying the upgrade.
