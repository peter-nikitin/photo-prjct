# Completed face vector retirement

The one-time AdaFace-only transition in [ADR 0054](../adr/0054-retire-sface-and-fix-adaface-vector-dimension.md) completed on 2026-10-04. This is a completion record, not an executable runbook; the retirement commands have been removed from the application.

The first deployment exposed a stale event-generation selection in Cyclingrace Вечернее Садовое. The corrected gallery read and submission path was deployed after removing an obsolete JSON-vector release gate. A visible photo then presented 11 faces and a gallery-face search was accepted; the test submission was rolled back. The public event gallery returned HTTP 200. The subsequent cleanup command was optimized for sequential batches before the remaining historical material was removed.

The final production read-back showed:

- 147,774 AdaFace vectors and zero old-model vectors.
- Zero old embedding arrays in historical processing payloads.
- `processing_faceembeddingvector.vector` physically typed as `vector(512)`.
- No former JSON embedding table or event generation column.
- Three face crops on a sampled photo after the schema contraction.

The pre-transition database dump was restored successfully in an isolated verification database before cleanup and remains in root-private storage on the canonical VM. Historical processing records, detections and saved search results were preserved. The 73 photos without an accepted current face projection remain an accepted recognition difference and did not block contraction. Recovery now requires a compatible AdaFace-only image or restoration of the verified pre-transition backup together with a compatible image; an old model image cannot run against the contracted database.
