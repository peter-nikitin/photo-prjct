# Distinguish race bibs from numbers printed on apparel

## Observed gap

Bib generation 1 accepts a candidate when OCR and the visual reader return the same digit string.
That confirms what the crop says, but it does not reliably establish that the digits belong to a
race bib. In the 28-photo canonical Gagarin/Metelsky cohort, search for `65` returns three photos
where `65` is part of the event-shirt artwork rather than the participant's paper race bib:
`00007_Vlad.jpg`, `00012_Vlad.jpg`, and `00016_Vlad.jpg`.

The production run otherwise completed all 28 bib, face, metadata, and preview jobs with no error or
retry. It retained all 42 pairs from the saved Linux-worker baseline and added the apparel `65` on
`00007_Vlad.jpg`. The saved reference is incomplete, so these observations do not establish global
precision or recall.

## Why this is non-blocking now

The accepted first version is an event-level opt-in for new uploads. Exact bib search remains useful
with the observed extra results, the false positives do not hide correctly matched photos, and bib
failure never removes an otherwise publishable photo. Operators can leave bib search disabled for
events whose apparel or signage contains prominent numbers. The first version has no promise of
perfect precision and no combined bib/face ranking where a false bib would affect an identity
cluster.

## Improvement hypotheses

Evaluate these as alternatives rather than stacking them without evidence:

1. Give a semantic validator a wider crop and require a structured decision containing both
   `is_race_bib` and the number. The current tight crop is sufficient to read `65` but often omits
   the paper plate boundary needed to classify it.
2. Detect plate evidence separately from digit recognition: a bounded rectangular paper region,
   attachment to the torso or waist, and the relationship between digits and the plate. Keep this
   decision outside OCR so OCR remains a simple digit reader.
3. Calibrate acceptance by contextual evidence and number length. Short numbers are valid race bibs,
   so a blanket minimum length would remove legitimate results; it may be useful only together with
   positive plate evidence.
4. Treat repeated apparel-like numbers across an event as a review signal. Do not reject by
   frequency alone because one participant can legitimately appear in many photos.
5. Compare a deterministic binary plate classifier with the existing Qwen visual pass before
   changing the production contract. Measure retained known positives, removed known negatives,
   latency, and memory on the same inputs.

## Revisit trigger

Bring this back into scope when any of the following occurs:

- a real event reports enough extra search results from apparel, signage, clocks, or phone numbers
  to make exact bib search misleading;
- bib recognition becomes enabled by default rather than explicitly enabled per event;
- bib evidence becomes an input to combined selfie, cluster, or ranking logic, where a false number
  can expand results beyond a direct exact match; or
- manual bib correction or suppression is scheduled for implementation.

## Likely scope

- extend the saved Istra and Gagarin/Metelsky references with complete positive and negative labels;
- add the three apparel `65` cases as mandatory negative controls;
- prototype the semantic validation alternatives without complicating OCR;
- version the worker/result contract if the accepted evidence shape changes;
- rerun both saved corpora plus one unseen event through the production-equivalent Linux worker;
- require the three apparel results to be rejected while retaining every currently reviewed real
  race bib, with no new processing errors and measured latency and memory within the accepted
  worker budget.
