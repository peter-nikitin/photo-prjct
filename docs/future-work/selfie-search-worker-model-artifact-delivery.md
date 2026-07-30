# Define private face-model delivery for container workers

## Observed gap

YuNet and SFace inference reads `PHOTO_WORKER_YUNET_MODEL_PATH` and
`PHOTO_WORKER_SFACE_MODEL_PATH`, but the current worker image contains no private model files and the
Compose worker services do not mount or forward model paths. The real-model end-to-end test succeeds
only when locally supplied files are passed directly to the host worker process.

## Why this does not block the current task

The repository implementation and real selfie-query application/worker contract are locally verified.
`PHOTO_PROCESSING_FACE_ENABLED` and `SELFIE_SEARCH_ENABLED` remain disabled by default, and this task
does not activate a container worker or staging selfie search. Adding private or license-sensitive
weights to source control would violate the current artifact boundary.

## Revisit trigger

Bring this into scope before enabling `selfie_query` in the exact Compose worker image selected for
the public selfie-search rollout. The delivery must be durable, reviewed, and verified in that image;
this trigger does not reopen already recorded legacy `face_embedding` staging evidence.

## Likely scope

Choose a reviewed, license-compatible way to place immutable model files inside the worker runtime;
pass only container-valid paths; fail startup or activation preflight when either file is missing or
unexpected; and verify selfie queries in the exact rollout image before enabling the feature flag.
