# Event Photo Gallery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render completed uploads for a published free event as an accessible gallery and stream their private originals through stable small- and large-preview application URLs.

**Architecture:** The event-detail view selects eligible `Photo` rows and converts them through a database-only `GalleryPhotoFactory` into immutable presentation values. A separate event-scoped media view rechecks eligibility, resolves either preview variant to the original during this transitional increment, and streams it through the existing private-storage adapter without exposing an object key. A later processing increment can change only resolver selection; this plan does not design or implement that processing boundary.

**Tech Stack:** Django 6.0.6, PostgreSQL 16, boto3 1.40.76, templates/CSS, locally vendored GLightbox 3.3.1, pytest, Node 22, Playwright 1.61.0.

## Global Constraints

- Use exactly `GalleryPhoto(photo_id, preview_media_small, preview_media_large, alt)`; each media value has a stable URL and variant `preview-small` or `preview-large`.
- Both variants resolve to the original now. Add no derivative fields/schema, readiness state, S3 readiness probe, job, worker, broker, or task framework.
- Gallery membership is database-only: requested published free event, `src=""`, non-null `original_key`, existing complete uploaded-row shape, stable ID ordering.
- Never render legacy `src` rows or paid originals. Add no search, recognition, selection, pricing, cart, purchase, entitlement, or download behavior.
- URLs are deterministic unsigned same-origin routes. Never expose `original_key`, redirect to S3, or return credentials, ETags, or raw storage errors.
- Support complete GET only: no Range, conditional GET, response ETag, attachment, or public caching.
- Vendor GLightbox 3.3.1 and its MIT license under `src/backend/static/ui/`; use no CDN and do not restore `src/proto`.
- Create no migration.

---

- Date: 2026-07-18
- Status: In progress — Tasks 1-6 complete; Tasks 7-8 not started
- Owner: project maintainer
- Related specification: [Event photo gallery design](../superpowers/specs/2026-07-18-event-photo-gallery-design.md)
- Related architecture: [Current architecture — implemented](../architecture.md#current-architecture--implemented),
  [Target MVP architecture — proposed](../architecture.md#target-mvp-architecture--proposed),
  [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing),
  [Purchase and download](../architecture.md#purchase-and-download),
  [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries),
  [Evolution stages](../architecture.md#evolution-stages), and
  [Open decisions](../architecture.md#open-decisions)
- Related ADRs: [0001](../adr/0001-django-modular-monolith.md),
  [0002](../adr/0002-postgresql-system-of-record.md),
  [0006](../adr/0006-yandex-object-storage-media.md),
  [0013](../adr/0013-use-direct-private-object-storage-ingestion.md),
  [0014](../adr/0014-keep-stage-2-ingestion-request-driven.md), and
  [0015](../adr/0015-allow-anonymous-free-event-original-delivery.md)
- ADR impact: Resolved — conforms to accepted ADR 0015 and applicable ADRs 0001, 0002, 0006,
  0013, and 0014.
- Completion evidence: Tasks 1-6 are implemented in the inclusive commit range
  `676bbf2^..0ee60ac`; Task 6's
  final visual verification passed all 43 tests.

## Scope

### In scope

- Immutable presentation values, pure factory, event query, private media resolver/stream.
- Responsive cards, empty state, local lightbox, JavaScript fallback, focused accessibility/browser/visual tests.
- Propagation of existing private-media bucket credentials through both deployment workflows and `deploy/apply-deployment.sh`.
- Read-only verification that the already-authorized external IAM policy permits final-prefix `GetObject` before gallery activation.
- Architecture and job evidence updates after verification.

### Out of scope

- Future processing and all its execution/schema/readiness decisions; paid previews and commerce.
- Pagination, custom chronology, intrinsic dimensions, responsive sources, Range/conditional requests.
- Cloud/IAM/CORS/lifecycle changes and seed-object creation. If final-prefix `GetObject` is absent, changing IAM requires a separate explicitly authorized operational action outside this plan.

## File map

- Create `src/backend/picflow/gallery.py` and `src/backend/picflow/tests/test_gallery.py`: presentation, resolver, streaming.
- Modify `src/backend/ingestion/storage.py`, `src/backend/ingestion/tests/fakes.py`, and `src/backend/ingestion/tests/test_storage.py`: validated final-object open.
- Modify `src/backend/config/views.py`, `src/backend/config/urls.py`, and `src/backend/picflow/tests/test_views.py`: query and endpoint.
- Modify `src/backend/templates/catalog/event_detail.html`, `src/backend/templates/ui/base.html`, and `src/backend/static/ui/catalog.css`; create the event-gallery and pinned GLightbox assets/license under `src/backend/static/ui/`.
- Modify `package.json`/`package-lock.json`; create `tests/js/event-gallery.test.js`.
- Modify `tests/visual/views.py`, `tests/visual/urls.py`, `tests/test_visual_reference.py`, and `tests/visual/visual.spec.js`; add desktop/mobile populated/empty snapshots under `tests/visual/visual.spec.js-snapshots/`.
- Modify `.github/workflows/deploy.yml`, `.github/workflows/promote-production.yml`,
  `deploy/apply-deployment.sh`, `docker-compose.prod.yml`, `.env.example`,
  `tests/deployment/test_deployment_scripts.py`, and `tests/test_repository_foundation.py`.
- Modify `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md`.

## Acceptance criteria

- Only eligible uploaded photos for the requested published free event render in ID order; page construction makes no S3 call.
- The template consumes only `GalleryPhoto`; each card has a normal large-preview link and small-preview image URL.
- Successful media GET streams full bytes with exact safe headers. Every request rechecks publication/eligibility.
- Missing/ineligible media is 404; pre-header storage failure is empty 503; mid-stream failure closes and logs only event slug/photo ID.
- HTML/response headers contain no object key, permanent S3 URL, credential, ETag, or raw exception.
- Keyboard/pointer open, next/previous, Escape/control close, focus restoration, touch, and no-JavaScript fallback pass.
- Inspected desktop/mobile populated/empty snapshots and focused Python/JS/deploy/visual checks pass.
- Before any service switch, a one-off container from the candidate immutable `APP_IMAGE` uses the
  requested temporary deployment environment and existing Compose network/database while canonical
  `.env` remains untouched. If no eligible `Photo` exists it prints only
  `gallery-private-media-preflight-skipped:no-eligible-photo` and exits zero. If a row exists it must
  open its `originals/*` object, read exactly one nonempty byte, close it, and print only
  `gallery-private-media-preflight-ok`; `AccessDenied` or any other read failure exits nonzero before
  env promotion, `stop nginx`, or candidate `compose up`, leaving canonical env, services, and
  deployment markers untouched.
- The no-eligible-photo skip permits an empty production deployment but is not evidence of storage permission. The first later eligible production upload is accepted only after an immediate media-route smoke request succeeds and media 404/503 plus safe stream-termination monitoring remains clean.

## Implementation

### Task 1: Immutable presentation contract

**Files:** Create `src/backend/picflow/gallery.py`, `src/backend/picflow/tests/test_gallery.py`.

**Interfaces:** Produce `GalleryVariant = Literal["preview-small", "preview-large"]`; frozen `GalleryMedia(url, variant)`; frozen `GalleryPhoto(photo_id, preview_media_small, preview_media_large, alt)`; `GalleryPhotoFactory.from_photo(*, photo: Photo, event_slug: str) -> GalleryPhoto`.

```python
GalleryVariant = Literal["preview-small", "preview-large"]

@dataclass(frozen=True)
class GalleryMedia:
    url: str
    variant: GalleryVariant

@dataclass(frozen=True)
class GalleryPhoto:
    photo_id: str
    preview_media_small: GalleryMedia
    preview_media_large: GalleryMedia
    alt: str
```

- [x] Add `test_gallery_values_are_frozen` and `test_factory_builds_stable_variant_urls_without_storage` with exact field/URL/alt assertions and patched `boto3.client` call count zero.
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py -k 'gallery_values or factory'`; expected RED is `ModuleNotFoundError: No module named 'picflow.gallery'`.
- [x] Add the exact dataclasses above and `GalleryPhotoFactory.from_photo`; its local `media(variant)` calls `reverse("photo_media", kwargs={"slug": event_slug, "photo_id": photo.pk, "variant": variant})`, constructs both variants, and uses `alt=f"Фото {photo.pk} с события {photo.event.name}"`. Add no query or storage import.
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py -k 'gallery_values or factory'`; expected GREEN is `2 passed` with no selected failure.
- [x] Commit: `git add src/backend/picflow/gallery.py src/backend/picflow/tests/test_gallery.py && git commit -m "feat: define event gallery presentation contract"`.

### Task 2: Validated final-object open

**Files:** Modify `src/backend/ingestion/storage.py`, `src/backend/ingestion/tests/fakes.py`, `src/backend/ingestion/tests/test_storage.py`.

**Interfaces:** Add `ReadableBody.read(amt: int | None = None) -> bytes`/`close()`; frozen `OpenedObject(body: ReadableBody, size: int, content_type: Literal["image/jpeg", "image/png"])`; `PrivateUploadStorage.open_final(*, key: str) -> OpenedObject`.

- [x] Add `test_open_final_returns_validated_stream_without_reading_it`, `test_open_final_rejects_invalid_key_before_client_call`, `test_open_final_closes_body_when_response_metadata_is_invalid`, and parametrized error-mapping tests. Extend `FakeBody.read(self, amt: int | None = None)` and make `FakeS3Client.get_object` return `Body`, `ContentLength`, and `ContentType` when no Range is supplied.
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/ingestion/tests/test_storage.py -k 'open_final'`; expected RED is `AttributeError: 'PrivateUploadStorage' object has no attribute 'open_final'`.
- [x] Add the typed `ReadableBody` protocol/`OpenedObject` dataclass and implement `open_final`: validate with `_validate_final_key`; call `get_object(Bucket=self._bucket, Key=key)` once; require callable `read`/`close`, non-boolean nonnegative integer length, and normalized exact JPEG/PNG content type; close the body before `ObjectMismatch` on invalid metadata; map client/SDK failures with existing sanitized exceptions. Do not read bytes or add list/write/copy/delete authority.

  ```python
  def open_final(self, *, key: str) -> OpenedObject:
      _validate_final_key(key)
      try:
          response = self._client.get_object(Bucket=self._bucket, Key=key)
      except ClientError as error:
          raise _mapped_error(error) from None
      except BotoCoreError:
          raise StorageUnavailable() from None
      body = response.get("Body")
      try:
          size = response["ContentLength"]
          raw_content_type = response["ContentType"].strip().lower()
          if isinstance(size, bool) or not isinstance(size, int) or size < 0:
              raise TypeError
          if raw_content_type == "image/jpeg":
              content_type: Literal["image/jpeg", "image/png"] = "image/jpeg"
          elif raw_content_type == "image/png":
              content_type = "image/png"
          else:
              raise TypeError
          if not callable(body.read) or not callable(body.close):
              raise TypeError
      except (KeyError, AttributeError, TypeError):
          if callable(getattr(body, "close", None)):
              body.close()
          raise ObjectMismatch() from None
      return OpenedObject(body=body, size=size, content_type=content_type)
  ```
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/ingestion/tests/test_storage.py -k 'open_final'`; expected GREEN is all selected tests passed. Then run `DB_HOST=127.0.0.1 pytest -q src/backend/ingestion/tests/test_storage.py`; expected GREEN is the complete module passed.
- [x] Commit: `git add src/backend/ingestion/storage.py src/backend/ingestion/tests/fakes.py src/backend/ingestion/tests/test_storage.py && git commit -m "feat: open validated private photo originals"`.

### Task 3: Transitional resolver and close-safe iterator

**Files:** Modify `src/backend/picflow/gallery.py`, `src/backend/picflow/tests/test_gallery.py`.

**Interfaces:** `FinalObjectStorage.open_final(*, key: str) -> OpenedObject`; `PublicMediaResolver(storage: FinalObjectStorage)`; frozen `ResolvedPublicMedia(body: ReadableBody, content_length: int, content_type: Literal["image/jpeg", "image/png"], extension: Literal["jpg", "png"])`; `CloseableMediaIterator(*, media: ResolvedPublicMedia, event_slug: str, photo_id: str, chunk_size: int = 65536)` implementing `Iterator[bytes]` plus `close() -> None`.

```python
class FinalObjectStorage(Protocol):
    def open_final(self, *, key: str) -> OpenedObject: ...

@dataclass(frozen=True)
class ResolvedPublicMedia:
    body: ReadableBody
    content_length: int
    content_type: Literal["image/jpeg", "image/png"]
    extension: Literal["jpg", "png"]

class PublicMediaResolver:
    def __init__(self, storage: FinalObjectStorage) -> None:
        self._storage = storage

    def resolve(self, *, photo: Photo, variant: GalleryVariant) -> ResolvedPublicMedia:
        if variant not in GALLERY_VARIANTS or not photo.original_key:
            raise ValueError("ineligible gallery media")
        opened = self._storage.open_final(key=photo.original_key)
        extension: Literal["jpg", "png"] = (
            "jpg" if opened.content_type == "image/jpeg" else "png"
        )
        return ResolvedPublicMedia(
            body=opened.body,
            content_length=opened.size,
            content_type=opened.content_type,
            extension=extension,
        )

class CloseableMediaIterator(Iterator[bytes]):
    def __init__(
        self,
        *,
        media: ResolvedPublicMedia,
        event_slug: str,
        photo_id: str,
        chunk_size: int = 65536,
    ) -> None:
        self._body = media.body
        self._event_slug = event_slug
        self._photo_id = photo_id
        self._chunk_size = chunk_size
        self._closed = False

    def __iter__(self) -> Self:
        return self
```

- [x] Add exact failing tests `test_resolver_maps_both_variants_to_original`, `test_resolver_rejects_unknown_variant_before_storage`, `test_resolver_requires_original_key_before_storage`, `test_iterator_closes_after_eof`, `test_iterator_closes_and_sanitizes_read_failure`, `test_iterator_close_before_first_next_closes_body`, and `test_iterator_close_after_partial_read_closes_body`. The pre-iteration test must instantiate the iterator, call `close()` without `iter()`/`next()`, and assert the body is closed exactly once.
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py -k 'resolver or iterator'`; expected RED is import failure for `FinalObjectStorage`/`CloseableMediaIterator`.
- [x] Implement `PublicMediaResolver.resolve`: reject variants outside `GALLERY_VARIANTS`; reject falsey `original_key`; call only `self._storage.open_final(key=photo.original_key)`; map content type to a typed safe extension; return the exact frozen fields above. Both variants deliberately take this identical branch.
- [x] Implement `CloseableMediaIterator` as an owning object, not a generator. Store the body during `__init__`; `close()` closes it once even before the first `next`; `__next__` raises `StopIteration` when closed, otherwise reads one chunk, closes/raises `StopIteration` at EOF, and on `Exception` logs only `Public photo stream ended early` with `extra={"event_slug": ..., "photo_id": ...}`, closes, and raises `StopIteration`. This explicit owner fixes the generator-before-first-next leak and lets `StreamingHttpResponse.close()` find the iterator's `close` method.

  ```python
  def __next__(self) -> bytes:
      if self._closed:
          raise StopIteration
      try:
          chunk = self._body.read(self._chunk_size)
      except Exception:  # SDK bodies may fail after response headers
          logger.error(
              "Public photo stream ended early",
              extra={"event_slug": self._event_slug, "photo_id": self._photo_id},
          )
          self.close()
          raise StopIteration from None
      if chunk == b"":
          self.close()
          raise StopIteration
      return chunk

  def close(self) -> None:
      if not self._closed:
          self._closed = True
          self._body.close()
  ```
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py -k 'resolver or iterator'`; expected GREEN is all selected tests passed. Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py`; expected GREEN is the full module passed.
- [x] Commit: `git add src/backend/picflow/gallery.py src/backend/picflow/tests/test_gallery.py && git commit -m "feat: resolve gallery variants to private media"`.

### Task 4: Eligibility query and media endpoint

**Files:** Modify `src/backend/config/views.py`, `src/backend/config/urls.py`, `src/backend/picflow/tests/test_views.py`.

**Interfaces:** Event context `gallery_photos: tuple[GalleryPhoto, ...]`; named route; `_public_media_resolver() -> PublicMediaResolver` creates `PublicMediaResolver(storage=PrivateUploadStorage())`; `@require_GET photo_media(...)` injects that factory result and passes a `CloseableMediaIterator` directly to `StreamingHttpResponse`.

```python
path(
    "events/<str:slug>/photos/<str:photo_id>/media/<str:variant>/",
    views.photo_media,
    name="photo_media",
)
```

- [x] Add page tests `test_event_detail_builds_ordered_gallery_without_storage` and `test_event_detail_excludes_legacy_other_event_and_paid_originals`. Add endpoint tests named for success headers, each 404 boundary, missing-object 404, storage 503, mid-stream closure, and `test_photo_media_rejects_non_get_methods_before_storage` parametrized over `head`, `post`, `put`, `patch`, `delete`, and `options`. Patch `_public_media_resolver`; assert each non-GET returns 405 with `Allow: GET` and zero resolver/storage calls. Add `test_photo_media_response_close_before_iteration_closes_body` that calls `response.close()` before consuming `streaming_content`.
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_views.py -k 'gallery or photo_media'`; expected RED is missing `gallery_photos`/`photo_media` route (404 or `NoReverseMatch`).
- [x] Implement the page query exactly as `Photo.objects.filter(event=event, src="", original_key__isnull=False).select_related("event").order_by("id")` only for free events, factory-map to tuple, and never touch storage.
- [x] Implement `_public_media_resolver()` exactly as `return PublicMediaResolver(storage=PrivateUploadStorage())`. Decorate `photo_media` with `@require_GET` so every other method returns 405 before event lookup or resolver construction. Validate variant; load published free event/scoped eligible photo; call `_public_media_resolver().resolve(...)` before response construction; map `ObjectMissing` to empty 404 and other `StorageError` to bodyless 503; pass `CloseableMediaIterator(...)` directly as `streaming_content`; set only the approved headers.

  ```python
  def _public_media_resolver() -> PublicMediaResolver:
      return PublicMediaResolver(storage=PrivateUploadStorage())

  @require_GET
  def photo_media(request, slug: str, photo_id: str, variant: str) -> HttpResponse:
      if variant not in GALLERY_VARIANTS:
          return HttpResponse(status=404)
      event = get_object_or_404(
          Event.objects.published(), slug=slug, access_type=Event.AccessType.FREE
      )
      photo = get_object_or_404(
          Photo, pk=photo_id, event=event, src="", original_key__isnull=False
      )
      try:
          media = _public_media_resolver().resolve(
              photo=photo, variant=cast(GalleryVariant, variant)
          )
      except ObjectMissing:
          return HttpResponse(status=404)
      except StorageError:
          return HttpResponse(status=503)
      stream = CloseableMediaIterator(
          media=media, event_slug=event.slug, photo_id=photo.pk
      )
      response = StreamingHttpResponse(stream, content_type=media.content_type)
      response["Content-Length"] = str(media.content_length)
      response["Content-Disposition"] = (
          f'inline; filename="photo-{photo.pk}.{media.extension}"'
      )
      response["Cache-Control"] = "private, no-store"
      response["X-Content-Type-Options"] = "nosniff"
      return response
  ```
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_views.py -k 'gallery or photo_media'`; expected GREEN is all selected tests passed. Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/ingestion/tests/test_storage.py`; expected GREEN is all three modules passed.
- [x] Commit: `git add src/backend/config/views.py src/backend/config/urls.py src/backend/picflow/tests/test_views.py && git commit -m "feat: serve event-scoped gallery media"`.

### Task 5: Markup and local GLightbox

**Files:** Modify `src/backend/templates/catalog/event_detail.html`, `src/backend/templates/ui/base.html`, `src/backend/static/ui/catalog.css`, `src/backend/picflow/tests/test_views.py`, `package.json`, and `package-lock.json`; create `src/backend/static/ui/event-gallery.js`, `src/backend/static/ui/glightbox.min.js`, `src/backend/static/ui/glightbox.min.css`, `src/backend/static/ui/GLIGHTBOX-LICENSE.txt`, and `tests/js/event-gallery.test.js`.

**Interfaces:** Cards are `.gallery-card-link.glightbox`, `href=preview_media_large.url`, image `src=preview_media_small.url`, `data-gallery="event-photos"`; initializer options `{selector: ".event-gallery .glightbox", touchNavigation: true, loop: false}`.

- [x] Add Django tests `test_event_detail_gallery_markup_and_loading_policy` and `test_event_detail_empty_gallery_is_accessible`, plus Node tests `initializes GLightbox once with local gallery options` and `does nothing without root or GLightbox`. Assert the first four images are eager/high and later images lazy.
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_views.py -k 'gallery_markup or empty_gallery'`; expected RED is missing `Фотографии`/gallery markup. Run `node --test tests/js/event-gallery.test.js`; expected RED is the initializer test failing to load absent `src/backend/static/ui/event-gallery.js`.
- [x] Pin `"glightbox": "3.3.1"` in devDependencies and run `npm install --package-lock-only`; expected GREEN is exit zero and lockfile version 3.3.1. Run `gallery_vendor_tmp="$(mktemp -d)"; npm pack glightbox@3.3.1 --pack-destination "$gallery_vendor_tmp"; tar -xzf "$gallery_vendor_tmp/glightbox-3.3.1.tgz" -C "$gallery_vendor_tmp"; cp "$gallery_vendor_tmp/package/dist/js/glightbox.min.js" src/backend/static/ui/glightbox.min.js; cp "$gallery_vendor_tmp/package/dist/css/glightbox.min.css" src/backend/static/ui/glightbox.min.css; cp "$gallery_vendor_tmp/package/LICENSE.md" src/backend/static/ui/GLIGHTBOX-LICENSE.txt`; expected GREEN is three nonempty destination files. Remove only the printed temporary directory after validating `package/package.json` says 3.3.1/MIT; commit no archive.
- [x] Append template markup using `{% for photo in gallery_photos %}` and `forloop.counter <= 4` for eager/high attributes, otherwise lazy; anchors use large URL and images small URL/alt. Add `{% block extra_js %}{% endblock %}` immediately before `</body>` in `ui/base.html` and override it in event detail to load local GLightbox then `event-gallery.js`. Implement the initializer with one DOM-ready call and the exact options above.
- [x] Add CSS with `aspect-ratio: 4 / 3`, `object-fit: cover`, explicit four/three/two/one column breakpoints, visible `:focus-visible`, 44px minimum interactive target, and `@media (prefers-reduced-motion: reduce)` transition/animation suppression.
- [x] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_views.py -k 'gallery_markup or empty_gallery'`; expected GREEN is selected tests passed. Run `node --test tests/js/event-gallery.test.js`; expected GREEN is all tests passed. Run `SECRET_KEY=test DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=127.0.0.1 DB_PORT=5432 python src/backend/manage.py collectstatic --noinput`; expected GREEN includes successful collection and no manifest error.
- [x] Commit: `git add package.json package-lock.json src/backend/templates/catalog/event_detail.html src/backend/templates/ui/base.html src/backend/static/ui/catalog.css src/backend/static/ui/event-gallery.js src/backend/static/ui/glightbox.min.js src/backend/static/ui/glightbox.min.css src/backend/static/ui/GLIGHTBOX-LICENSE.txt src/backend/picflow/tests/test_views.py tests/js/event-gallery.test.js && git commit -m "feat: add accessible event gallery lightbox"`.

### Task 6: Browser and visual coverage

**Files:** Modify `tests/visual/views.py`, `tests/visual/urls.py`, `tests/test_visual_reference.py`, and `tests/visual/visual.spec.js`; add `tests/visual/visual.spec.js-snapshots/desktop-event-gallery-populated.png`, `desktop-event-gallery-empty.png`, `mobile-event-gallery-populated.png`, and `mobile-event-gallery-empty.png` in that snapshot directory.

**Interfaces:** Add `/__visual__/event/gallery-populated/` and `gallery-empty/` named `visual_event_gallery_populated`/`empty`; fixtures match presentation values and use local static images only.

- [x] Add `test_visual_routes_have_deterministic_names_and_paths` entries and Playwright tests named `gallery supports keyboard navigation and focus restoration`, `gallery supports mobile swipe`, and `gallery fallback link works without JavaScript`, covering the exact interactions and a separate `javaScriptEnabled: false` context.
- [x] Run `DB_HOST=127.0.0.1 pytest -q tests/test_visual_reference.py -k 'routes or orm'`; expected RED identifies missing gallery route names. Add ORM-free frozen/mapping fixtures and exact populated/empty routes with local static images.
- [x] Run `DB_HOST=127.0.0.1 pytest -q tests/test_visual_reference.py -k 'routes or orm'`; expected GREEN is selected contract tests passed. Run `npm run test:visual`; expected RED reports exactly four missing gallery snapshot files while existing snapshots remain matched.
- [x] Run `npm run test:visual:update`; inspect all four at 1440x1000 and 390x844 for 4:3 layout, no overflow, empty state, IDs, and unchanged header. Correct/regenerate if needed.
- [x] Run `npm run test:visual`; expected GREEN is every interaction and snapshot passed with zero console/request/resource failures.
- [x] Commit: `git add tests/visual/views.py tests/visual/urls.py tests/visual/visual.spec.js tests/visual/visual.spec.js-snapshots tests/test_visual_reference.py && git commit -m "test: cover event gallery interactions and visuals"`.

### Task 7: Deployment propagation

**Files:** Modify `.github/workflows/deploy.yml`, `.github/workflows/promote-production.yml`,
`deploy/apply-deployment.sh`, `docker-compose.prod.yml`,
`tests/deployment/test_deployment_scripts.py`, `tests/test_repository_foundation.py`, and
`.env.example`.

**Interfaces:** Propagate `PRIVATE_MEDIA_S3_BUCKET` variable and private access/secret secrets;
existing endpoint/region feed Django's private aliases. Build requested settings in a private
mode-0600 temp file while canonical `.env` stays untouched. `compose_with_env_file` supplies that
same absolute path through both Compose `--env-file` and service selector `APP_ENV_FILE`;
`docker-compose.prod.yml` defaults the selector to `.env` for normal operations. Before stopping
Nginx or reconciling services, the helper pulls only candidate `web` and runs the exact
`compose run --rm --no-deps -T --entrypoint python web` one-off with
`manage.py shell --no-imports -c "$gallery_media_preflight"` on the existing project
network/database. Only a successful gate
atomically promotes the temp to canonical `.env`. Candidate failure removes the temp and changes no
canonical env, service, certificate, cron, or marker state. Failures after promotion retain the
existing image-only rollback semantics; this task does not claim full environment restoration.

- [ ] Add `test_apply_propagates_private_media_read_settings`,
  `test_candidate_private_media_preflight_skips_when_no_eligible_photo`,
  `test_candidate_private_media_preflight_reads_when_photo_exists`,
  `test_candidate_private_media_preflight_runs_before_service_switch`, parametrized
  `test_failed_candidate_private_media_preflight_leaves_canonical_env_untouched`,
  `test_candidate_pull_failure_leaves_canonical_env_without_service_reconciliation`,
  `test_workflows_forward_private_media_settings`, behavioral
  `test_deployment_path_performs_no_iam_mutation`, and Compose selector coverage in
  `test_production_compose_uses_an_immutable_application_image`. Assert requested bucket/access/secret
  reach the candidate container without logging their values. Assert no-row exact output and zero
  storage calls; eligible-row exact output plus one `open_final`, one-byte read, and close; pull/run
  ordering before env promotion/stop/up; and exact `manage.py shell --no-imports -c`. Pull or gallery
  failure must preserve canonical `.env` bytes and metadata plus old image/deployment/project markers,
  remove requested temp files, and perform zero stop, certificate, cron, or `compose up` action. The
  IAM test proves no `yc`, `aws`, `s3cmd`, policy, role-binding, or bucket-policy mutation path.
- [ ] Run `pytest -q tests/deployment/test_deployment_scripts.py -k 'private_media'`; expected RED shows missing `PRIVATE_MEDIA_S3_BUCKET` and no candidate one-off `compose run` before `stop nginx`.
- [ ] Add workflow env entries for bucket variable plus access/secret secrets and include all three in `envs`. Add exact shell output lines for `PRIVATE_MEDIA_S3_BUCKET`, `PRIVATE_MEDIA_S3_ACCESS_KEY_ID`, and `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY`, retaining empty defaults and existing endpoint/region; never echo values to logs. After registry login but before `compose stop nginx`, pull only `web` and execute this read-only gate:

  ```sh
  compose_with_env_file() {
      compose_env_file="$1"
      shift
      APP_ENV_FILE="$compose_env_file" \
          docker compose --project-name "$COMPOSE_PROJECT_NAME" \
              --env-file "$compose_env_file" \
              -f "$DEPLOY_ROOT/docker-compose.prod.yml" \
              -f "$overlay_file" "$@"
  }

  if ! compose_with_env_file "$requested_env_tmp" pull web; then
      fail "Candidate application image pull failed"
  fi

  gallery_media_preflight='
  from contextlib import closing
  from ingestion.storage import PrivateUploadStorage
  from picflow.models import Event, Photo
  try:
      photo = Photo.objects.filter(
          event__publication_status=Event.PublicationStatus.PUBLISHED,
          event__access_type=Event.AccessType.FREE,
          src="",
          original_key__isnull=False,
      ).order_by("id").first()
  except Exception:
      raise SystemExit("Gallery private-media read prerequisite failed") from None
  if photo is None:
      print("gallery-private-media-preflight-skipped:no-eligible-photo")
  else:
      try:
          opened = PrivateUploadStorage().open_final(key=photo.original_key)
          with closing(opened.body) as body:
              if not body.read(1):
                  raise RuntimeError
      except Exception:
          raise SystemExit("Gallery private-media read prerequisite failed") from None
      print("gallery-private-media-preflight-ok")
  '
  if ! compose_with_env_file "$requested_env_tmp" run --rm --no-deps -T --entrypoint python web \
      manage.py shell --no-imports -c "$gallery_media_preflight"; then
      fail "Candidate image failed private-media read prerequisite"
  fi

  mutation_started=1
  mv "$requested_env_tmp" "$DEPLOY_ROOT/.env"
  requested_env_tmp=""
  ```

  `--entrypoint python` bypasses the image entrypoint, so this candidate container performs no
  migration, seed, group bootstrap, collectstatic, or Gunicorn startup. `--no-deps` reuses the
  already-running Compose network/database without starting or replacing a service. The old web,
  Nginx, canonical env, and markers remain untouched throughout the gate. Candidate failure only
  removes the requested temp. Success promotes it and continues with existing `compose stop nginx`,
  certificate reconciliation, remaining pull, `compose up`, and image-only post-switch rollback.
- [ ] Run `pytest -q tests/deployment/test_deployment_scripts.py -k 'private_media or
  candidate_pull_failure or apply_success'`; expected GREEN is all selected tests passed. Run
  `pytest -q tests/deployment/test_deployment_scripts.py`,
  `pytest -q tests/test_repository_foundation.py`, `sh -n deploy/apply-deployment.sh`, and
  `dash -n deploy/apply-deployment.sh`; expected GREEN is zero failures and zero syntax exits.
- [ ] Run the exact Compose verification command:

  ```sh
  APP_IMAGE=example.invalid/photo-prjct:config-check \
    docker compose --env-file .env.example \
      -f docker-compose.prod.yml -f docker-compose.https.yml config >/dev/null
  ```

  Expected GREEN is exit zero without pulling an image or creating/changing a container. Do not add
  `APP_IMAGE` to `.env.example`; it is a per-verification placeholder and a real deployment input.
- [ ] Commit the scoped workflows, apply script, production Compose selector, env example, and
  behavioral/config tests with the verified Task 7 implementation.

### Task 8: Architecture and ADR reconciliation

**Files:** Modify `docs/architecture.md`, `docs/product-jobs.md`, `docs/engineering-jobs.md`.

**Interfaces:** Consumes verified Tasks 1-7 evidence; produces current-architecture wording, PJ-005 status/evidence, and matching engineering capability evidence without expanding runtime scope.

- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/ingestion/tests/test_storage.py tests/test_visual_reference.py tests/deployment/test_deployment_scripts.py`; expected GREEN is all selected modules passed.
- [ ] Run `ruff format --check . && ruff check . && mypy`; expected GREEN is three zero exits. Run `python src/backend/manage.py check && python src/backend/manage.py makemigrations --check --dry-run`; expected GREEN is no system-check issue and `No changes detected`. Run `npm run test:js && npm run test:visual`; expected GREEN is all JS/visual tests passed.
- [ ] Compare delivered behavior with the approved specification, accepted ADR 0015, all other
  applicable ADRs, and `docs/architecture.md`.
- [ ] Record only verified gallery/private-stream behavior. Update PJ-005 and matching engineering evidence without claiming derivatives, paid previews, downloads, commerce, or unperformed staging activation.
- [ ] Stop for a new decision rather than contradicting an accepted ADR; supersede rather than
  edit an accepted decision.
- [ ] Run `git diff --check` and scan browser-facing files for original keys, credentials, and permanent S3 URLs; expect none.
- [ ] Confirm status contains no migration, worker/broker, derivative/readiness schema, purchase behavior, or unrelated file.
- [ ] Record the architecture and ADR reconciliation outcome in the pull request before push.
- [ ] Commit: `git add docs/architecture.md docs/product-jobs.md docs/engineering-jobs.md && git commit -m "docs: record event gallery delivery evidence"`.

## Verification

    DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/ingestion/tests/test_storage.py tests/test_visual_reference.py tests/deployment/test_deployment_scripts.py
    npm run test:js
    npm run test:visual
    ruff format --check .
    ruff check .
    mypy
    python src/backend/manage.py check
    python src/backend/manage.py makemigrations --check --dry-run
    sh -n deploy/apply-deployment.sh
    git diff --check

Expected: zero exits; no migration; no browser failures; only the four inspected additions change snapshots; no private key/S3 destination appears in HTML. CI is authoritative for full PostgreSQL coverage (at least 80%) and the digest-pinned visual container.

## Operational impact and rollout

- Configure the existing private bucket/access/secret in each GitHub Environment before activation. The same credentials currently serve ingestion, so their existing permissions must remain unchanged by this delivery.
- Treat final-prefix `GetObject` as an external prerequisite, not an IAM implementation step.
  `apply-deployment.sh` checks it with the Task 7 candidate-image one-off after pulling requested
  immutable `APP_IMAGE` but before canonical env promotion, `stop nginx`, or `compose up`. The one-off
  uses the requested temp through both Compose env selectors, the existing network/database, and
  candidate `open_final` code while bypassing the normal entrypoint. With an eligible row, success is
  exactly one nonempty byte plus `gallery-private-media-preflight-ok`; it prints no key, credential,
  ETag, or SDK detail and mutates no object, row, policy, role, ACL, or service.
- A fresh production database with no eligible row is not a storage failure: the one-off prints only `gallery-private-media-preflight-skipped:no-eligible-photo` and exits zero without constructing `PrivateUploadStorage` or making an S3 call. Staging rollout after verified reference-photo reconciliation expects `gallery-private-media-preflight-ok`, not the skip result.
- If an eligible row exists and the one-off encounters missing configuration, `AccessDenied`,
  `StorageUnavailable`, a missing/empty object, or any other open/read/close failure, it emits only
  `Gallery private-media read prerequisite failed`, exits nonzero, deletes the requested temp, and
  stops before canonical env, old Nginx/web, certificate, cron, or markers are touched; no recovery
  reconciliation runs. After a successful gate, canonical env promotion enters the existing
  deployment path whose rollback restores only the prior `APP_IMAGE`, not the full previous env.
  Do not add a role, edit a policy, change a bucket ACL, or broaden credentials under this plan. Any
  IAM correction requires a new explicitly authorized operational scope; after it is completed
  externally, rerun the deployment/preflight.
- After a production environment previously recorded the skip outcome, its first future eligible upload requires an immediate public media-route smoke check and monitoring of media 404/503 plus `Public photo stream ended early`. A failed first-read smoke check disables further gallery activation/traffic and starts the separately authorized IAM/storage correction path; the empty-database skip is never treated as proof of `GetObject` permission.
- Missing/invalid config leaves event HTML available but media requests sanitized 503; deployment health alone is insufficient.
- Deploy the immutable image through existing staging workflow/`apply-deployment.sh`; smoke-test populated/empty event, both variants, unknown/paid/draft 404, no key/redirect, and safe log/503 behavior.
- Monitor media 404/503 rate, `Public photo stream ended early` count, latency, web health, and bucket GetObject errors without logging private details.
- Promote the exact staging-verified image through the manual production workflow only after production private settings are valid.
- Existing URLs/pages stay compatible; stable media URLs survive a later resolver-only derivative change.

## Rollback

- Redeploy the previous immutable image through `apply-deployment.sh`; its existing recovery path restores the prior image on failed health.
- Leave propagated variables: old code ignores them. Rotate credentials only on suspected exposure.
- No migration/object write/delete/irreversible effect exists. Do not make the bucket public or redirect to S3 as a workaround.

## Open questions

- None.
