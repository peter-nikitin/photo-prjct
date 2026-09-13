# Bib-number recognition activation

Status: implemented and functionally verified in an isolated Docker full cycle. Production
activation is blocked until the same acceptance command passes on production-equivalent Linux and
the first canonical event passes the observation gates below.

The accepted local Istra artifact is `var/bib-search/production-istra-011` (ignored local data).
All 37 photos remained published and all 37 bib jobs succeeded with zero retries, failures, lease
expiry, restarts, OOM, or public-health failures. It retained 40 accepted, 24 rejected, and zero
uncertain candidates, recovered every user-confirmed number, returned no user-rejected pair, and
passed exact search, leading-zero, and event-isolation checks. Bib total time was 31.684 seconds p50,
58.501 seconds p95, and 70.909 seconds maximum. Peak cgroup memory was 3,607,793,664 bytes, which
selects 5120 MiB with the required 30% headroom and 2 CPUs. The regression anchors include an empty
result for `00001_Vlad.jpg` and exact number `259` for `00012_Vlad.jpg`.

That artifact is still RED for capacity because Docker Desktop recorded 12,351 swap-in and 18,913
swap-out pages. It establishes recognition quality and candidate sizing, not Linux capacity or
release readiness. A read-only inventory of the canonical x86_64 VM found 4 CPUs, 15,992 MiB RAM,
14,207 MiB available at the sample, no configured swap, and 59 GiB free on `/`; its two current
workers were idle at about 28.6 MiB each under 2 GiB limits. Inventory is not cohort evidence, and
an arm64 local image digest cannot identify a rebuilt amd64 release image. The current deployed
worker configuration remains bib-free at 1 CPU and 2 GiB per worker.

## Fixed boundaries

- Every event starts with `bib_search_enabled=false`. Deploying code, changing worker identity, or
  changing worker resources never enables an event.
- The event checkbox directly shows or hides the public number form. Its value is copied when each
  photo is confirmed. Toggling it later never creates, cancels, retries, deletes, or reprocesses work
  for an existing photo.
- Bib work starts after normal photo publication. Empty success, retry, or terminal bib failure does
  not unpublish the photo. Attempts and sanitized error codes remain available to the event report.
- Public search is exact and event-scoped. `7` and `007` are different. The bib and selfie forms are
  separate requests.
- Initial operation uses one worker replica and total worker concurrency one. Face, bib, preview,
  and metadata work therefore do not overlap within this deployment.
- This runbook has no backfill, broad requeue, purge, reset, or manual-correction step. Preserve all
  policies, jobs, attempts, errors, projections, and published photos during stop or rollback.

The pinned bib identity is `1/bib_recognition/1`. Its inference configuration SHA-256 is
`32b3f2c94202df7c90e5c799c9ca21b760d5d2ac9c376fe578f6f330e415edf9`. The visual runtime uses
llama.cpp revision `5266f24da75dc449bd56cbed7addb9c8e4a6a73e`, Qwen artifact revision
`d38d39f5972e27cd58023f9b1e9f994b0c85ca47`, model SHA-256
`089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae`, and projector SHA-256
`f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82`. Django's complete
generation-1 job configuration SHA-256 is
`bd0207374a2b36e36247597ee1e09cb255996005daf07dc400ffbff9885023e3`. The llama.cpp source
tarball SHA-256 is `2de0d87eda4696e9f6bbd771d4c623267f4e95856cce6f99793f91522f993e43`.
The RapidOCR detector, recognizer, classifier, and dictionary SHA-256 values are respectively
`4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae`,
`5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5`,
`e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c`, and
`17665d27ed39f0deb82007859992d626d3105d0ee4578c120b7c72138dc04d05`.

## 1. Pass the Linux resource gate

Run the accepted harness from the exact release checkout on x86_64 Linux with Docker, using the
private checksum-matched Istra source directory. Choose a new output path; the command refuses to
overwrite an artifact.

```sh
docker build -f Dockerfile.worker -t findme-photo-worker:bib-local .
scripts/local-bib-acceptance.sh \
  --event-slug local-istra-bib \
  --source-root "$LINUX_ISTRA_SOURCE_ROOT" \
  --baseline experiments/bib_search/worker_acceptance/baseline_manifest.json \
  --output var/bib-search/production-istra-linux-001
jq '{status,failures,selected_memory_mib,selected_worker_cpus,replicas}' \
  var/bib-search/production-istra-linux-001/comparison.json
```

Proceed only when `status` is `GREEN`, `failures` is empty, the selected values are 5120 MiB and 2
CPUs, and the saved evidence covers the complete cohort through the final public query. A different
peak may select another 512 MiB increment; stop for review if the selected limit differs from 5120
MiB or exceeds 6 GiB. Record the Linux image ID, host architecture, event wall time, stage p50/p95,
peak RSS/cgroup memory, minimum host available memory, swap deltas, CPU/iowait saturation, restarts,
OOM, lease expiry, disk headroom, health failures, and pre-run/cohort web p95.

Do not equate this rebuilt image ID with the arm64 local image. Bind the Linux artifact to the exact
release commit, baseline hash, pinned identities above, and its own image ID.

## 2. Deploy the code with every event disabled

Merge and deploy through the canonical **Deploy** workflow. For this first dark deployment, leave
the existing bib-free worker identity and existing worker resource values unchanged. Do not enable
an event in Admin. An exact-SHA retry is:

```sh
gh workflow run deploy.yml --ref main -f deployment_sha="$RELEASE_SHA"
gh run watch "$DEPLOY_RUN_ID"
```

After the workflow is green, verify the exact application marker, services, migrations, and that no
event is enabled. Run the remote commands read-only:

```sh
ssh -l petrnikitin 111.88.151.64 'sudo cat /opt/photo-prjct/deployed-image'
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml ps'
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T web python manage.py showmigrations picflow processing'
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T web python manage.py shell --no-imports -c "from picflow.models import Event; print(list(Event.objects.filter(bib_search_enabled=True).values_list(\"slug\", flat=True)))"'
curl -fsS https://findme-photo.ru/health/
```

The migration output must show `picflow.0015_bib_search_policy` and
`processing.0009_bib_reading_projection` applied. The event query must print `[]`. The marker must
match the exact SHA-tagged application image reported by the workflow, and public health must be
`{"status": "ok"}`. Stop before activation on any mismatch.

## 3. Configure the measured worker only after Linux GREEN

Record the current non-secret repository variables and the current `/opt/photo-prjct/.env` values so
rollback can restore the exact prior identity, type order, replica count, CPU, and memory:

```sh
gh variable get PHOTO_WORKER_PROCESSOR_IDENTITIES
gh variable get PHOTO_WORKER_PROCESSOR_TYPES
gh variable get PHOTO_WORKER_REPLICAS
gh variable get PHOTO_WORKER_CPUS
gh variable get PHOTO_WORKER_MEMORY_LIMIT
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sed -n "/^PHOTO_WORKER_\(PROCESSOR_IDENTITIES\|PROCESSOR_TYPES\|REPLICAS\|CPUS\|MEMORY_LIMIT\)=/p" .env'
```

For the first rollout, the recorded non-bib identity list must be exactly
`1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,2/face_embedding/3,3/face_embedding/5,1/selfie_query/2`.
The recorded processor type list must remain exactly
`selfie_query,face_embedding,capture_metadata,generate_preview`; bib is polled through its explicit
identity and must not be added to this priority type list. Preserve the complete identity list in
the same order and append bib. If either recorded value differs, stop and reconcile the deployment
contract rather than replacing it with the example below. With the verified current values, set
the measured contract:

```sh
gh variable set PHOTO_WORKER_PROCESSOR_IDENTITIES --body '1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,2/face_embedding/3,3/face_embedding/5,1/selfie_query/2,1/bib_recognition/1'
gh variable set PHOTO_WORKER_PROCESSOR_TYPES --body 'selfie_query,face_embedding,capture_metadata,generate_preview'
gh variable set PHOTO_WORKER_REPLICAS --body '1'
gh variable set PHOTO_WORKER_CPUS --body '2'
gh variable set PHOTO_WORKER_MEMORY_LIMIT --body '5120m'
gh workflow run deploy.yml --ref main -f deployment_sha="$RELEASE_SHA"
gh run watch "$DEPLOY_RUN_ID"
```

Keep every event disabled during this deployment. Verify one worker, its exact configured image,
resource limits, non-restarting/OOM state, processor identity, and pinned model files:

```sh
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml ps worker'
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && c=$(sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml ps -q worker); sudo docker inspect --format "image={{.Config.Image}} id={{.Image}} running={{.State.Running}} restarting={{.State.Restarting}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} memory={{.HostConfig.Memory}} nano_cpus={{.HostConfig.NanoCpus}}" "$c"'
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sed -n "/^PHOTO_WORKER_\(PROCESSOR_IDENTITIES\|PROCESSOR_TYPES\|REPLICAS\|CPUS\|MEMORY_LIMIT\)=/p" .env'
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T worker python -c "from photo_worker.contracts import BIB_INFERENCE_CONFIGURATION_SHA256; from photo_worker.bib_visual import LLAMA_CPP_REVISION,MODEL_REVISION; print(BIB_INFERENCE_CONFIGURATION_SHA256,LLAMA_CPP_REVISION,MODEL_REVISION)"'
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T worker sha256sum /worker/models/bib/Qwen3VL-2B-Instruct-Q4_K_M.gguf /worker/models/bib/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf /worker/models/bib/ch_PP-OCRv5_det_mobile.onnx /worker/models/bib/ch_PP-OCRv5_rec_mobile.onnx /worker/models/bib/ch_ppocr_mobile_v2.0_cls_mobile.onnx /worker/models/bib/dictionary.txt'
```

The container must report 5,368,709,120 memory bytes and 2,000,000,000 nano-CPUs. The configured
identity line must exactly equal
`PHOTO_WORKER_PROCESSOR_IDENTITIES=1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,2/face_embedding/3,3/face_embedding/5,1/selfie_query/2,1/bib_recognition/1`;
this comparison verifies every preserved processor, including watermarked-preview generation, as
well as the appended bib processor. The configured type line must exactly equal
`PHOTO_WORKER_PROCESSOR_TYPES=selfie_query,face_embedding,capture_metadata,generate_preview`.
The configured image and image ID must match the workflow's amd64 worker artifact. The printed
revisions and all six file hashes must exactly match this runbook; the workflow build log must also
contain the network-disabled non-root runtime, face, preview, and bib model smoke successes. Re-run
the disabled-event query from step 2 and require `[]`.

## 4. Observe the first event

Choose exactly one first event and record its slug and expected new-upload denominator. In Django
Admin, enable bib search for that event only, save, and immediately verify that it is the sole enabled
event. This checkbox mutation is the activation point. It does not enroll older photos.

```sh
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T web python manage.py shell --no-imports -c "from picflow.models import Event; print(list(Event.objects.filter(bib_search_enabled=True).values_list(\"slug\", flat=True)))"'
date -u +%Y-%m-%dT%H:%M:%SZ
```

Start the host/container and public-latency observation before uploading. Record the initial worker
image/state, `/proc/meminfo`, `/proc/vmstat` `pswpin`/`pswpout`, `df -B1 /`, a pre-upload public
latency sample, and `vmstat 5 61`. Upload only the bounded first-event cohort through the ordinary
photographer page at `https://findme-photo.ru/photographer/uploads/`; do not create Photo or job rows
manually. Keep capturing worker RSS/CPU, restart/OOM state, host memory/swap/CPU/iowait, disk, and
public health through the last terminal bib result and final public query.

After terminal processing, save the UTC end time and the exact event report:

```sh
date -u +%Y-%m-%dT%H:%M:%SZ
ssh -l petrnikitin 111.88.151.64 \
  'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T web python manage.py report_event_bib_processing --event-slug "'$EVENT_SLUG'"'
curl -fsS "https://findme-photo.ru/events/$EVENT_SLUG/?bib=$KNOWN_BIB" >/dev/null
curl -fsS "https://findme-photo.ru/events/$EVENT_SLUG/?bib=$LEADING_ZERO_COUNTERQUERY" >/dev/null
curl -fsS https://findme-photo.ru/health/
```

The cohort record must include event slug, photo denominator, UTC interval, application and worker
image identities, bib processor/configuration identity, state/result/error counts, retries, stage
and total p50/p95, worker peak RSS/CPU, host minimum available memory and swap deltas, sustained
CPU/iowait intervals, restarts/OOM, disk minimum, health failures, and web p95 against its pre-run
baseline. Inspect search results in the event gallery for known exact and leading-zero cases; a
query must never return another event's photo.

## 5. Stop conditions

Do not start or continue uploads, and do not enable a second event, when any of these is true:

- deployed image, migration, processor/configuration, model hash, resource, or single-replica
  identity differs from the reviewed release;
- a photo is unpublished or otherwise loses ordinary gallery eligibility because bib processing
  succeeds, returns empty, retries, or fails;
- reviewed numbers are missed, rejected junk is searchable, an unreviewed change lacks manual
  inspection, leading zeros change, or results cross event/eligibility boundaries;
- the worker is OOM-killed or unexpectedly restarts, a valid bib lease expires, worker peak RSS is
  above 70% of 5120 MiB, host `MemAvailable` falls below 1 GiB, or any swap-in/out occurs;
- host CPU stays above 85% for more than five minutes, iowait stays above 10% for more than five
  minutes, disk headroom is unsafe, a public probe fails, or web p95 exceeds twice the pre-run p95;
- the event report is incomplete, malformed, contains an unexpected identity, or leaves an
  unexplained queued/processing/retry-wait/failed state.

A bib recognition failure remains a retained processing outcome; it does not justify hiding or
deleting the photo. Review its sanitized error code and coverage impact. Keep the event disabled
for further new uploads until the finding is understood. Any algorithm, prompt, model, decoding, or
validation change requires a new pinned configuration generation and a fresh full-cycle artifact;
existing photos are not automatically reprocessed.

## 6. Stop and rollback

First stop new uploads for the affected event and disable its bib checkbox in Admin. This hides the
public field and prevents bib enrollment only for photos confirmed afterward; it does not cancel
already requested work. Preserve the event report and host/container observations.

If the application is healthy and the issue is confined to bib recognition, remove only
`1/bib_recognition/1` from the repository identity variable. Leave
`PHOTO_WORKER_PROCESSOR_TYPES` at the unchanged exact four-type value
`selfie_query,face_embedding,capture_metadata,generate_preview`; bib was never added to it. Restore
the exact recorded pre-activation replica/CPU/memory values, and redeploy the exact reviewed SHA
through **Deploy**. Restore two 1 CPU/2 GiB workers only after the bib identity is absent. Verify the
prior preview/face identities, unchanged type list, worker state, event-disabled query, and public
health.

If the candidate application or shared worker image is unsafe, deploy the exact prior application
SHA through the ordinary rollback path after all affected events are disabled. Keep the additive
schema, immutable per-photo policies, jobs, attempts, errors, and `BibReading` rows. Do not reverse
the migrations, purge data, reset state, issue a broad requeue, or edit recognized numbers.

## 7. Second event

Enable and upload the explicitly selected second event only after the first event has a complete
saved cohort, every stop condition is clear, and no quality, publication, privacy, capacity,
latency, or identity finding remains unresolved. Keep one worker replica, 2 CPUs, 5120 MiB, and
total concurrency one. Record the same denominator, interval, identities, report, resource/health
metrics, exact queries, and event-isolation evidence. A clean first event is a prerequisite, not a
substitute for the second event's own evidence.
