# Production bib recognition and event-scoped number search

- Date: 2026-09-12
- Status: Approved by maintainer on 2026-09-12
- Related architecture: [Current architecture](../../architecture.md#current-architecture--implemented), [Accepted constraints](../../architecture.md#accepted-constraints), [Photo ingestion and indexing](../../architecture.md#photo-ingestion-and-indexing), [Search](../../architecture.md#search)
- Related ADRs: [0002](../../adr/0002-postgresql-system-of-record.md), [0003](../../adr/0003-docker-compose-yandex-cloud.md), [0017](../../adr/0017-use-django-polled-photo-processing-jobs.md), [0019](../../adr/0019-use-public-event-selfie-search.md), [0028](../../adr/0028-operate-one-canonical-deployment.md), [0033](../../adr/0033-keep-durable-knowledge-test-executable-contracts.md)
- ADR impact: Conforms to ADRs 0002, 0003, 0017, 0028, and 0033. ADR 0019 is related public-search precedent but does not govern bib queries. The design keeps one Django/PostgreSQL control plane, one canonical Docker Compose deployment, and one private polling worker. The event checkbox, exact query, pinned model runtime, and concurrency-one scheduling are reversible implementation and rollout choices; no new or superseding ADR is required.
- Predecessors: local bib experiment and local worker-integration specifications at commit
  `4102ab0` on branch `codex/local-bib-search-test`
- Product job: [PJ-007 — Customer — Find photos by bib](../../product-jobs.md#pj-007--customer--find-photos-by-bib)

## 1. Outcome

For selected events, recognize participant bib numbers on newly uploaded photographs in the same
private processing pipeline as face recognition. Store searchable, versioned evidence in
PostgreSQL and add a public event-scoped exact-number search beside the existing selfie search.

The first production version has four deliberate limits:

1. recognition applies only to photos confirmed while the event checkbox is enabled;
2. changing the checkbox never backfills or reinterprets an existing photo;
3. search returns direct exact bib matches only; and
4. there is no production UI for correcting, accepting, or suppressing a machine result.

The worker must be a release-ready Linux/CPU Docker worker. The accepted macOS/MLX path remains
quality evidence and a source implementation, not a production runtime.

## 2. Existing evidence and constraints

The local experiment and worker integration processed 37 Istra photos and 28 Gagarin photos through
the ordinary upload and processing boundaries. Their frozen reports contain 67/57 candidates and
41/42 accepted number pairs. The user decisions in those reports remain the regression reference;
the unreadable `00011_Vlad.jpg · 67` pair is excluded from quality scoring.

The accepted algorithm uses original-image tiling, RapidOCR candidate generation, visual reading,
and an independent exact validator. A reduced full-image preview was faster but lost seven real bibs
in the inspected Gagarin comparison, so bib inference continues to read the private original.

The macOS worker recorded native compute p50 7.926 seconds and p95 9.603 seconds per photo. Its
sampled parent/child RSS peaked at approximately 2.8 GiB, excluding GPU and system memory. These
figures do not predict Linux throughput or authorize the current deployment worker's 2 GiB limit.

The current gallery contract publishes a preview-backed photo immediately after Django accepts its
required preview. Face recognition is subsequently queued and does not govern gallery eligibility.
Bib recognition must preserve that behavior: a missing or failed number affects bib search coverage,
not photo publication.

## 3. Scope

### Included

- An event checkbox, disabled by default, that controls both new-photo bib enrollment and public
  number-field visibility.
- An immutable per-photo snapshot of whether bib recognition applied at confirmation time.
- Automatic bib enrollment for applicable new uploads after the photo reaches its normal gallery-
  eligible transition.
- A Linux/CPU `bib_recognition` processor in the existing worker image and private API contract.
- Pinned RapidOCR/ONNX models and a pinned Qwen3-VL 2B Q4 visual model executed with a pinned
  `llama.cpp` build.
- One active worker job at a time across preview, face, bib, and other configured photo processors.
- Bounded immutable attempts, typed results, retryable failures, current projections, aggregate
  event statistics, and exact event-scoped search.
- A separate public GET form for a bib number beside the existing selfie form.
- Local Docker acceptance, one-event canonical-deployment observation, adjustment if required, and
  a second-event observation.

### Excluded

- Automatic or implicit backfill of any photo confirmed before event-level activation.
- Manual correction, confirmation, suppression, or moderation in the production application.
- Combining selfie and bib evidence, ranking by bib confidence, cluster expansion, appearance
  similarity, or treating a bib as an identity assertion.
- Fuzzy, partial, normalized-integer, cross-event, or multi-event bib search.
- Parallel ML jobs, a second worker replica, a broker, a GPU service, or a separate inference API.
- Reprocessing previous photos after an event checkbox or processor version changes.
- Synchronous inference in Django or a public request.

Future cluster search may use bib equality as one typed relationship between photos. This design
retains the necessary source attempt, model identity, confidence, and geometry, but defines no graph,
cluster, scoring, or expansion behavior.

## 4. Event and photo applicability

`Event` has one staff-managed bib-search checkbox in Django Admin. It defaults to disabled. The same
persisted value decides whether the public event page renders the bib form. The application does not
compute field visibility from existing results, processing states, storage objects, or counts.

At photo confirmation, Django copies the event value into an explicit immutable bib-processing
policy on the photo. The policy distinguishes at least:

- bib processing disabled for this photo; and
- original-image bib recognition generation 1 requested for this photo.

Later changes to the event do not modify that policy, create a job, cancel a job, or discard a
result. Therefore:

- disabling the event immediately hides its public bib form while keeping existing results and
  attempts;
- photos uploaded while disabled never acquire bib work automatically; and
- re-enabling the event restores the form and applies only to photos confirmed afterward.

The event checkbox is product configuration, not a code-owned release gate. Deployment and worker
identity configuration may keep the capability unavailable while every event remains disabled.
Verifying that the deployed worker can claim the pinned bib identity is an operator precondition for
enabling the first event.

## 5. Publication and enrollment flow

Bib work begins only after the normal application transition has made the photo eligible for its
gallery. For preview-first photos, the accepted preview transaction both publishes the derivative
and requests downstream recognition. For a photo generation that is immediately gallery eligible,
confirmation performs the equivalent post-publication enrollment.

```text
confirmed original
    -> normal preview/publication policy
    -> photo becomes gallery eligible
        +-> face recognition requested when applicable
        `-> bib recognition requested when photo bib policy requires it
```

Face and bib are independent processing states and attempts. Neither is a prerequisite for gallery
eligibility. A failure to enqueue downstream work must not roll back an already valid published
preview; reconciliation may request a missing job only from the photo's explicit persisted policy
and accepted publication state.

An accepted bib result updates the current searchable projection. Exhausted retries leave the bib
state failed, retain every attempt and error code, and leave the photo published. A successful
result with no accepted numbers is also terminal success and produces an empty projection. These
outcomes remain distinguishable in reporting.

## 6. Sequential worker and Linux inference runtime

The canonical worker retains total concurrency `1`. Its existing round-robin identity scheduler may
choose any configured ready job; the product does not require face-before-bib or bib-before-face for
one photo. The resource invariant is that two processor jobs never compute concurrently in one
worker, and the initial deployment runs one worker replica.

For one bib job, a bounded child process owns RapidOCR, tiling, and Qwen visual reading. It downloads
only the exact private original authorized by the current lease. The process exits after returning a
bounded candidate result or error, ensuring its model memory is released before another worker job
begins. Django performs the independent deterministic validation at the protected completion
boundary. Termination, deadline, and lease-loss handling must also terminate the complete child
process tree.

The Linux visual runtime is a pinned `llama.cpp` CPU build with a pinned Q4 GGUF conversion of
Qwen3-VL-2B-Instruct and its exact multimodal projector. The image build verifies all runtime,
model, OCR, dictionary, and configuration checksums and executes a non-networked model smoke as the
non-root worker user. Runtime model download is forbidden. The worker image contains no database
configuration, Django secret, permanent Object Storage credential, or public endpoint credential.

Multimodal support in `llama.cpp` is evolving. Model format, prompt template, decoding parameters,
thread count, image preprocessing, crop policy, response grammar, and all artifact hashes are part
of the immutable bib processor identity. A change creates a new processor/configuration generation
and never silently reinterprets stored evidence. There is no automatic fallback to Transformers,
MLX, another model, or another quantization.

## 7. Recognition and validation contract

The production processor preserves the accepted layered responsibility:

1. bounded original-image tiling and RapidOCR locate textual candidates;
2. Qwen3-VL visually reads digits from the associated image evidence;
3. deterministic Django validation accepts only an exact visual match for the OCR candidate; and
4. only accepted decisions enter the searchable projection.

The Qwen prompt performs visual reading. It does not decide product validity, event scope, storage,
publication, retries, or search membership. The validator remains a separate typed layer so rules
can be tested and revised without embedding application policy in a generative prompt.

The result records bounded candidate evidence including source checksum, processor and configuration
identity, OCR confidence, source geometry, crop geometry, raw bounded OCR evidence, bounded visual
response, inference timings, and accepted/rejected/uncertain validation decision. Digit strings are
ASCII-only, between 1 and 16 characters, and preserve leading zeros. `7` and `007` are distinct.

Telephone fragments, ordinary signs, clothing text, visual objects, and body parts must not enter
the projection unless the complete accepted pipeline produces an exact candidate. The acceptance
corpus specifically retains the previously reviewed false-positive examples as regression evidence;
there is no event-specific or number-specific blacklist.

## 8. Search projection and query

PostgreSQL remains authoritative. Immutable attempt JSON retains complete bounded evidence, while a
dedicated current bib-reading projection supports indexed search without querying attempt JSON.
Each projected row belongs to one photo, one accepted source attempt, one exact number, and its
accepted candidate evidence. Multiple different numbers may belong to one photo; repeated evidence
for the same photo and number produces one search membership.

Replacing the accepted current result for the same processor generation atomically replaces that
generation's projection. Historical attempts remain immutable. Failed and uncertain candidates do
not enter search membership. Search always begins with the ordinary eligible gallery queryset, then
filters its current event by exact projected number, so a projection cannot expose hidden,
unpublished, paid-ineligible, or cross-event media.

The public bib form is an independent GET form with one field and its own submit button. The selfie
form remains a separate request and contains no bib field; the bib form contains no file field.
Therefore the UI cannot submit both inputs together and the backend needs no priority or merge rule.
On narrow screens the forms may stack while remaining separate.

The query parameter is `bib`. Django trims surrounding whitespace, then requires 1–16 ASCII digits.
A valid query returns the ordinary numbered event gallery filtered to exact number equality. Page
links preserve the valid `bib` value. An empty query shows the unfiltered gallery; an invalid query
renders the event page with a field error and no bib-filtered result claim. The URL stores no search
object, result snapshot, confidence, or model detail.

## 9. Failure evidence and statistics

Existing job, lease, retry, stale-result, idempotence, and terminal-attempt semantics apply. Bib
adds bounded processor-specific errors for invalid input, download, OCR, visual runtime, timeout,
malformed response, and result-contract failure. Retryability remains code-owned and is never
accepted from an arbitrary worker payload.

Every event report can distinguish:

- applicable and non-applicable photos;
- queued, processing, retry-wait, succeeded, failed, and cancelled states;
- attempts and retries by sanitized error code;
- successful photos with zero candidates, zero accepted numbers, or one or more accepted numbers;
- candidate totals by accepted, rejected, and uncertain decision;
- download, OCR, visual, validation, and total duration distributions; and
- processor/configuration identities observed in the interval.

Reports expose aggregate counts and timings. They do not expose original keys, signed URLs, image
bytes, raw crops, worker credentials, or unbounded model output. The retained per-photo processing
state and immutable attempts allow a later operator workflow to inspect failures without requiring
that workflow in this release.

## 10. Release and observation sequence

The feature grows through four working states; later states must not be started when the preceding
state has unresolved quality, resource, publication, or privacy failures.

### 10.1 Release-ready Docker package

Build the complete Linux worker image and application behavior, including the disabled-default event
field, processor identity, pinned artifacts, public form, exact search, reports, and deployment
validation. The image must be deployable through the existing canonical workflow before real-event
activation begins.

### 10.2 Local full-cycle event

Run one complete event through Docker using ordinary upload, preview publication, face enrollment,
bib enrollment, persistence, report generation, and public exact search. Compare the resulting bib
decisions with the saved user-reviewed evidence from the prior local runs. The unreadable excluded
pair does not count as a miss.

The local gate requires no loss of user-confirmed numbers, no return of user-rejected junk, correct
event isolation and leading-zero behavior, and no regression in publication when bib succeeds,
returns no number, retries, or fails. A changed result outside the reviewed labels requires manual
inspection and an updated immutable comparison artifact before proceeding.

### 10.3 First canonical event

Deploy with every event checkbox disabled. Verify the exact deployed image, worker identity and
model hashes, then enable one explicitly selected event and upload its photos as new uploads. Record
the event, photo denominator, UTC interval, image digest, processor identity, result counts,
failures, retries, durations, worker RSS/CPU, host memory/swap, restarts/OOM, disk headroom, web
health, and request latency.

The first event is an observation cohort, not a hidden backfill. If evidence requires changes, ship
a new pinned processor generation and deployment. Existing photos retain their original attempts and
results; they are not automatically reprocessed.

### 10.4 Second canonical event

After the first event has no unresolved blocking quality or capacity finding, enable and upload a
second explicitly selected event. Record the same evidence to check that the first outcome was not
event-specific. Continue with one worker replica and total concurrency one unless a later measured
and approved design changes that boundary.

## 11. Capacity and activation gates

The current 2 GiB worker limit is not assumed to fit the Linux bib runtime. Local Docker measurement
selects a candidate limit; the first canonical event validates it on the VM. The final limit must
leave explicit headroom rather than match observed peak RSS.

Both local and canonical checks require:

- zero worker OOM kills and unexpected restarts;
- zero lease expiry caused by valid bib inference exceeding its configured deadline;
- worker peak RSS at or below 70% of its selected container limit;
- at least 1 GiB host `MemAvailable` and zero swap-in/swap-out during the cohort;
- no sustained host CPU saturation above 85% for more than five minutes and no sustained iowait
  above 10%;
- healthy public probes and web p95 latency no worse than twice the pre-run baseline; and
- recorded event wall-clock time and per-stage p50/p95 rather than an unmeasured throughput claim.

If a gate fails, keep affected event checkboxes disabled while retaining the deployed code and
evidence. Do not add parallelism, swap, a broker, or an automatic runtime fallback to conceal the
failure. A required VM resize or separate worker is a distinct operational change.

## 12. Acceptance criteria

1. The release worker image is Linux/CPU, non-root, reproducible, checksum-pinned, network-free at
   runtime for model acquisition, and can execute preview, current face, and bib identities.
2. One worker replica executes no more than one processing job at a time; face and bib inference do
   not overlap.
3. The event checkbox defaults off, controls public field visibility directly, and is copied into an
   immutable photo policy at confirmation.
4. Toggling the event never creates, cancels, deletes, or reprocesses work for existing photos.
5. A gallery-eligible applicable photo gets exactly one current bib job for its pinned generation;
   retries and reconciliation remain idempotent.
6. Bib success, empty success, retry, and terminal failure never remove an otherwise eligible photo
   from the gallery.
7. Terminal failures and uncertain candidates remain measurable by event and processor identity.
8. Only current accepted exact digit strings enter the indexed projection; leading zeros survive.
9. `?bib=` searches only ordinary eligible photos in the current event and preserves numbered-page
   navigation.
10. The bib and selfie forms are separate requests and cannot submit both inputs together.
11. The frozen local user decisions pass the Linux full-cycle comparison before deployment
    activation.
12. The first and second canonical events follow the recorded observation sequence without
    automatic backfill.

## 13. Rejected alternatives

### Parallel face and bib inference

Two workers or concurrent in-process tasks could reduce event wall-clock time, but the accepted
macOS bib path already exceeded the current 2 GiB container limit and Linux capacity is unknown.
Concurrency one is the smallest safe release boundary. Parallelism requires separate measured
evidence and design.

### Transformers/PyTorch visual runtime

Qwen supports a direct Transformers path, but the full Linux representation would increase worker
memory and packaging pressure. The selected pinned Q4 `llama.cpp` process is smaller and releases
all visual-model memory at the job boundary. There is no runtime fallback between the two.

### Separate GPU inference service

A GPU service could improve throughput but adds deployment, network, credential, availability, and
cost boundaries before measured demand requires them.

### OCR without visual reading

The reviewed runs show phone fragments, signs, clothing marks, objects, and body regions becoming
number candidates. Removing visual reading would knowingly restore those false positives.

### One combined face-and-bib processor

A combined result would couple model versions, retries, failure statistics, and future reprocessing.
Independent jobs preserve the established processing model while total concurrency one controls
resource use.

### One form that accepts both selfie and bib

The current release has no accepted merge or ranking rule. Separate forms make each request
unambiguous and avoid encoding a temporary priority that future clustered search would replace.

### Deriving field visibility from stored results

Counting results on every event page adds load and makes UI behavior depend on partial or failed
processing. The event checkbox is already the intended product decision and provides stable,
constant-cost visibility.

## 14. References

- [`llama.cpp` multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- [Qwen3-VL repository and deployment guidance](https://github.com/QwenLM/Qwen3-VL)
- [Qwen3-VL-2B-Instruct model card](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)
- [Worker VM sizing](../../photo-processing-vm-sizing.md)
