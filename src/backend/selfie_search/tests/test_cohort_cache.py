from __future__ import annotations

import importlib
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from threading import Event as ThreadEvent
from unittest.mock import patch
from uuid import UUID

import numpy as np
from django.test import SimpleTestCase
from picflow.models import Event
from processing.services import face_cohort
from selfie_search.services.ranking import RankingError


class CohortCacheTests(SimpleTestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module("selfie_search.services.cohort_cache")
        self.cache = self.module.CohortCache()
        self.event = Event(id=10)
        self.generations = (
            {
                "model": "sface",
                "contract_version": 3,
                "processor_type": "face_embedding",
                "processor_version": 3,
                "configuration": {"model": "sface"},
                "configuration_hash": "a" * 64,
            },
        )
        self.identity = face_cohort.FaceCohortIdentity(
            projection_id=UUID(int=1),
            embedding_id=UUID(int=2),
            detection_id=UUID(int=3),
            photo_id="photo",
            photo_event_id=10,
            attempt_event_id=10,
            attempt_photo_id="photo",
            attempt_id=UUID(int=4),
            job_id=UUID(int=5),
            run_id=UUID(int=6),
            contract_version=3,
            processor_version=3,
            configuration_hash="a" * 64,
            model_version="sface",
        )

    def get(self):
        return self.cache.get(
            event=self.event, generations=self.generations, model="sface", dimensions=2
        )

    def test_exact_identity_hits_and_matrix_is_immutable(self) -> None:
        with (
            patch.object(
                self.module, "load_compatible_face_identities", return_value=(self.identity,)
            ),
            patch.object(
                self.module,
                "iter_compatible_face_cohort",
                return_value=iter([(self.identity, [1.0, 0.0])]),
            ),
        ):
            cold = self.get()
            warm = self.get()
        self.assertFalse(cold.cache_hit)
        self.assertTrue(warm.cache_hit)
        self.assertIs(cold.entry, warm.entry)
        np.testing.assert_array_equal(cold.entry.matrix, [[1.0, 0.0]])
        self.assertEqual(cold.entry.matrix.dtype, np.float64)
        self.assertTrue(cold.entry.matrix.flags.c_contiguous)
        with self.assertRaises(ValueError):
            cold.entry.matrix[0, 0] = 0.0
        with self.assertRaises(ValueError):
            cold.entry.matrix.setflags(write=True)

    def test_same_count_replacement_and_configuration_change_miss(self) -> None:
        replacement = replace(self.identity, embedding_id=UUID(int=20), detection_id=UUID(int=30))
        with (
            patch.object(
                self.module,
                "load_compatible_face_identities",
                side_effect=[(self.identity,), (replacement,), (replacement,)],
            ),
            patch.object(
                self.module,
                "iter_compatible_face_cohort",
                side_effect=[
                    iter([(self.identity, [1.0, 0.0])]),
                    iter([(replacement, [0.0, 1.0])]),
                    iter([(replacement, [0.0, 1.0])]),
                ],
            ),
        ):
            first = self.get()
            old_matrix = weakref.ref(first.entry.matrix)
            del first
            second = self.get()
            self.assertIsNone(old_matrix())
            self.assertFalse(second.cache_hit)
            self.assertEqual(second.entry.fingerprint, (replacement,))
            np.testing.assert_array_equal(second.entry.matrix, [[0.0, 1.0]])
            self.generations[0]["configuration"] = {"model": "sface", "new": True}
            third = self.get()
            self.assertFalse(third.cache_hit)

    def test_concurrent_misses_publish_only_one_complete_entry(self) -> None:
        identities_read = Barrier(2)
        build_started = ThreadEvent()
        allow_build = ThreadEvent()

        def read(*args):
            identities_read.wait(timeout=5)
            return (self.identity,)

        def rows(*args):
            build_started.set()
            if not allow_build.wait(timeout=5):
                raise AssertionError("build was not released")
            yield self.identity, [1.0, 0.0]

        with (
            patch.object(self.module, "load_compatible_face_identities", side_effect=read),
            patch.object(self.module, "iter_compatible_face_cohort", side_effect=rows),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [executor.submit(self.get) for _ in range(2)]
            self.assertTrue(build_started.wait(timeout=5))
            self.assertFalse(any(future.done() for future in futures))
            allow_build.set()
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(sorted(result.cache_hit for result in results), [False, True])
        self.assertIs(results[0].entry, results[1].entry)
        np.testing.assert_array_equal(results[0].entry.matrix, [[1.0, 0.0]])

    def test_invalid_full_rows_fail_closed_without_publishing(self) -> None:
        invalid_rows = [
            (self.identity, [1.0]),
            (self.identity, [float("nan"), 0.0]),
            (self.identity, [float("inf"), 0.0]),
            (self.identity, [True, 0.0]),
            (self.identity, [0.5, 0.5]),
            (replace(self.identity, model_version="other"), [1.0, 0.0]),
            (replace(self.identity, photo_event_id=11), [1.0, 0.0]),
            (replace(self.identity, attempt_event_id=11), [1.0, 0.0]),
            (replace(self.identity, attempt_photo_id="other"), [1.0, 0.0]),
            (replace(self.identity, attempt_id=None), [1.0, 0.0]),  # type: ignore[arg-type]
        ]
        for bad_identity, vector in invalid_rows:
            with (
                self.subTest(identity=bad_identity, vector=vector),
                patch.object(
                    self.module, "load_compatible_face_identities", return_value=(bad_identity,)
                ),
                patch.object(
                    self.module,
                    "iter_compatible_face_cohort",
                    return_value=iter([(bad_identity, vector)]),
                ),
            ):
                with self.assertRaises(RankingError):
                    self.get()
        with (
            patch.object(
                self.module, "load_compatible_face_identities", return_value=(self.identity,)
            ),
            patch.object(
                self.module,
                "iter_compatible_face_cohort",
                return_value=iter([(self.identity, [1.0, 0.0])]),
            ),
        ):
            self.assertFalse(self.get().cache_hit)

    def test_build_uses_its_full_snapshot_if_membership_changes_after_identity_read(self) -> None:
        replacement = replace(self.identity, embedding_id=UUID(int=20))
        with (
            patch.object(
                self.module,
                "load_compatible_face_identities",
                side_effect=[(), (self.identity, replacement)],
            ),
            patch.object(
                self.module,
                "iter_compatible_face_cohort",
                return_value=iter([(self.identity, [1.0, 0.0]), (replacement, [0.0, 1.0])]),
            ),
        ):
            cold = self.get()
            warm = self.get()
        self.assertEqual(cold.entry.fingerprint, (self.identity, replacement))
        self.assertEqual(cold.entry.matrix.shape, (2, 2))
        self.assertTrue(warm.cache_hit)

    def test_empty_cohort_has_valid_empty_matrix(self) -> None:
        with (
            patch.object(self.module, "load_compatible_face_identities", return_value=()),
            patch.object(self.module, "iter_compatible_face_cohort", return_value=iter(())),
        ):
            result = self.get()
        self.assertEqual(result.entry.matrix.shape, (0, 2))
