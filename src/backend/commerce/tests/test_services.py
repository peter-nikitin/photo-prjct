from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event as ThreadEvent
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from picflow.models import Event, Photo

from commerce import services as commerce_services
from commerce.identity import browser_token_sha256
from commerce.models import Cart, CartItem
from commerce.services import clear_cart, read_cart, set_photo_selected


class CartServiceTests(TestCase):
    """The breaks caught here create state for failed commands or stale cart responses."""

    token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    other_token = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8"

    def setUp(self) -> None:
        self.event = self.event_named("Paid event", "paid-event")
        self.other_event = self.event_named("Other event", "other-event")
        self.photo = Photo.objects.create(id="photo-1", event=self.event, src="photos/photo-1.jpg")
        self.other_photo = Photo.objects.create(
            id="photo-2", event=self.event, src="photos/photo-2.jpg"
        )

    def event_named(self, name: str, slug: str) -> Event:
        return Event.objects.create(
            name=name,
            slug=slug,
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

    def purchasable(self, *photos: Photo):
        return patch(
            "commerce.services.purchasable_paid_photo_queryset",
            side_effect=lambda *, event, watermarked_previews_enabled: Photo.objects.filter(
                pk__in=[photo.pk for photo in photos], event=event
            ),
        )

    def test_read_with_no_or_malformed_token_creates_no_state(self) -> None:
        with self.purchasable(self.photo):
            absent = read_cart(
                event=self.event,
                browser_token=None,
                watermarked_previews_enabled=True,
            )
            malformed = read_cart(
                event=self.event,
                browser_token="not-a-token",
                watermarked_previews_enabled=True,
            )

        self.assertEqual(absent.item_count, 0)
        self.assertEqual(malformed.item_count, 0)
        self.assertEqual(Cart.objects.count(), 0)

    def test_first_eligible_add_creates_one_position_and_current_price_snapshot(self) -> None:
        now = timezone.now()

        with self.purchasable(self.photo):
            result = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=None,
                watermarked_previews_enabled=True,
                now=now,
            )

        cart = Cart.objects.get()
        self.assertTrue(result.changed)
        self.assertTrue(result.selected)
        self.assertIsNotNone(result.issued_browser_token)
        self.assertTrue(result.refresh_browser_token)
        self.assertFalse(result.delete_browser_token)
        self.assertEqual(result.snapshot.photo_ids, (self.photo.pk,))
        self.assertEqual(result.snapshot.unit_price_kopecks, 30000)
        self.assertEqual(result.snapshot.total_kopecks, 30000)
        self.assertEqual(cart.expires_at, now + timedelta(days=30))
        self.assertEqual(CartItem.objects.filter(cart=cart, photo=self.photo).count(), 1)

    def test_rejected_add_creates_no_token_cart_or_item(self) -> None:
        with self.purchasable():
            result = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=None,
                watermarked_previews_enabled=True,
            )

        self.assertFalse(result.changed)
        self.assertFalse(result.selected)
        self.assertIsNone(result.issued_browser_token)
        self.assertEqual(Cart.objects.count(), 0)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_carts_are_isolated_by_event_and_token(self) -> None:
        other_event_photo = Photo.objects.create(
            id="other-event-photo", event=self.other_event, src="photos/other-event.jpg"
        )
        with self.purchasable(self.photo, other_event_photo):
            set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
            set_photo_selected(
                event=self.other_event,
                photo_id=other_event_photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
            other_browser = read_cart(
                event=self.event,
                browser_token=self.other_token,
                watermarked_previews_enabled=True,
            )

        self.assertEqual(Cart.objects.count(), 2)
        self.assertEqual(other_browser.photo_ids, ())

    def test_explicit_duplicate_add_and_remove_are_idempotent_without_expiry_refresh(self) -> None:
        first_now = timezone.now()
        later = first_now + timedelta(hours=1)
        with self.purchasable(self.photo):
            first = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
                now=first_now,
            )
            duplicate = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
                now=later,
            )
            expiry_after_no_op = Cart.objects.get().expires_at
            removed = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=False,
                browser_token=self.token,
                watermarked_previews_enabled=True,
                now=later,
            )
            absent = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=False,
                browser_token=self.token,
                watermarked_previews_enabled=True,
                now=later + timedelta(hours=1),
            )

        self.assertTrue(first.changed)
        self.assertFalse(duplicate.changed)
        self.assertEqual(expiry_after_no_op, first_now + timedelta(days=30))
        self.assertTrue(removed.changed)
        self.assertTrue(removed.delete_browser_token)
        self.assertFalse(absent.changed)
        self.assertFalse(absent.refresh_browser_token)
        self.assertEqual(Cart.objects.count(), 0)

    def test_read_prunes_ineligible_positions_in_addition_order_without_extending_expiry(
        self,
    ) -> None:
        now = timezone.now()
        with self.purchasable(self.photo, self.other_photo):
            set_photo_selected(
                event=self.event,
                photo_id=self.other_photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
                now=now,
            )
            set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
                now=now + timedelta(seconds=1),
            )
        expiry = Cart.objects.get().expires_at

        with self.purchasable(self.photo):
            result = read_cart(
                event=self.event,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )

        self.assertEqual(result.photo_ids, (self.photo.pk,))
        self.assertTrue(result.pruned)
        self.assertEqual(Cart.objects.get().expires_at, expiry)

    def test_pruning_the_last_item_deletes_the_cart(self) -> None:
        with self.purchasable(self.photo):
            set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
        with self.purchasable():
            result = read_cart(
                event=self.event,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )

        self.assertTrue(result.pruned)
        self.assertTrue(result.delete_browser_token)
        self.assertEqual(Cart.objects.count(), 0)

    def test_removing_final_item_keeps_cookie_when_another_unexpired_event_cart_exists(
        self,
    ) -> None:
        other_event_photo = Photo.objects.create(
            id="other-event-photo", event=self.other_event, src="photos/other-event.jpg"
        )
        with self.purchasable(self.photo, other_event_photo):
            set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
            set_photo_selected(
                event=self.other_event,
                photo_id=other_event_photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
            result = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=False,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )

        self.assertTrue(result.changed)
        self.assertFalse(result.delete_browser_token)
        self.assertEqual(Cart.objects.count(), 1)

    def test_clear_deletes_only_the_current_event_cart_and_refreshes_retained_cookie(self) -> None:
        other_event_photo = Photo.objects.create(
            id="other-clear-photo", event=self.other_event, src="photos/other-clear.jpg"
        )
        with self.purchasable(self.photo, other_event_photo):
            set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
            set_photo_selected(
                event=self.other_event,
                photo_id=other_event_photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
            result = clear_cart(event=self.event, browser_token=self.token)

        self.assertTrue(result.changed)
        self.assertTrue(result.refresh_browser_token)
        self.assertFalse(result.delete_browser_token)
        self.assertEqual(result.snapshot.photo_ids, ())
        self.assertEqual(Cart.objects.filter(event=self.other_event).count(), 1)

    def test_expired_cart_is_logically_empty_before_cleanup_and_new_add_replaces_it(self) -> None:
        expired = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(self.token),
            event=self.event,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        CartItem.objects.create(cart=expired, photo=self.photo)

        with self.purchasable(self.photo):
            before_cleanup = read_cart(
                event=self.event,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
            self.assertTrue(Cart.objects.filter(pk=expired.pk).exists())
            replacement = set_photo_selected(
                event=self.event,
                photo_id=self.photo.pk,
                selected=True,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )

        self.assertEqual(before_cleanup.photo_ids, ())
        self.assertTrue(replacement.changed)
        self.assertEqual(replacement.snapshot.photo_ids, (self.photo.pk,))


class CartServiceConcurrencyTests(TransactionTestCase):
    """The breaks caught here duplicate positions or return a mixed-price response."""

    token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="Concurrent paid event",
            slug="concurrent-paid-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.photo = Photo.objects.create(
            id="concurrent-photo",
            event=self.event,
            src="photos/concurrent.jpg",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        self.other_event = Event.objects.create(
            name="Other concurrent paid event",
            slug="other-concurrent-paid-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.other_photo = Photo.objects.create(
            id="other-concurrent-photo",
            event=self.other_event,
            src="photos/other-concurrent.jpg",
        )

    def purchasable(self):
        return patch(
            "commerce.services.purchasable_paid_photo_queryset",
            side_effect=lambda *, event, watermarked_previews_enabled: Photo.objects.filter(
                pk__in=(self.photo.pk, self.other_photo.pk),
                event=event,
            ),
        )

    def mutate(self, *, selected: bool, event: Event | None = None, photo: Photo | None = None):
        event = event or self.event
        photo = photo or self.photo
        close_old_connections()
        try:
            return set_photo_selected(
                event=Event.objects.get(pk=event.pk),
                photo_id=photo.pk,
                selected=selected,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )
        finally:
            close_old_connections()

    def test_concurrent_duplicate_adds_leave_one_item_and_consistent_snapshots(self) -> None:
        with self.purchasable(), ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.mutate(selected=True), range(2)))

        self.assertEqual(CartItem.objects.filter(photo=self.photo).count(), 1)
        self.assertEqual({result.snapshot.unit_price_kopecks for result in results}, {30000})
        self.assertTrue(all(result.snapshot.total_kopecks == 30000 for result in results))

    def test_opposite_desired_states_never_create_duplicate_positions(self) -> None:
        with self.purchasable():
            self.mutate(selected=True)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda selected: self.mutate(selected=selected),
                        (True, False),
                    )
                )

        self.assertLessEqual(CartItem.objects.filter(photo=self.photo).count(), 1)
        self.assertTrue(all(result.snapshot.item_count in (0, 1) for result in results))
        self.assertTrue(all(result.snapshot.total_kopecks in (0, 30000) for result in results))

    def test_ineligible_add_during_a_concurrent_mutation_never_commits_a_position(self) -> None:
        with (
            patch(
                "commerce.services.purchasable_paid_photo_queryset",
                return_value=Photo.objects.none(),
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lambda _: self.mutate(selected=True), range(2)))

        self.assertEqual(CartItem.objects.filter(photo=self.photo).count(), 0)
        self.assertTrue(all(not result.changed for result in results))

    def test_concurrent_price_edit_returns_one_complete_price_calculation(self) -> None:
        with self.purchasable():
            self.mutate(selected=True)

            def edit_price() -> None:
                close_old_connections()
                try:
                    Event.objects.filter(pk=self.event.pk).update(price_per_photo_kopecks=45075)
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=2) as executor:
                response = executor.submit(self.mutate, selected=True)
                editor = executor.submit(edit_price)
                result = response.result()
                editor.result()

        self.assertIn(result.snapshot.unit_price_kopecks, (30000, 45075))
        self.assertEqual(
            result.snapshot.total_kopecks,
            result.snapshot.unit_price_kopecks * result.snapshot.item_count,
        )

    def test_concurrent_final_removals_report_one_digest_wide_cookie_deletion(self) -> None:
        with self.purchasable():
            self.mutate(selected=True)
            self.mutate(selected=True, event=self.other_event, photo=self.other_photo)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda args: self.mutate(selected=False, event=args[0], photo=args[1]),
                        ((self.event, self.photo), (self.other_event, self.other_photo)),
                    )
                )

        self.assertEqual(Cart.objects.count(), 0)
        self.assertEqual(sum(result.delete_browser_token for result in results), 1)

    def test_final_removal_serializes_with_first_add_in_another_event_for_the_same_token(
        self,
    ) -> None:
        decision_ready = ThreadEvent()
        release_decision = ThreadEvent()
        original_should_delete = commerce_services._should_delete_browser_token

        def delayed_decision(*, digest: str, now):
            result = original_should_delete(digest=digest, now=now)
            decision_ready.set()
            self.assertTrue(release_decision.wait(timeout=2))
            return result

        with self.purchasable():
            self.mutate(selected=True)
            with (
                patch(
                    "commerce.services._should_delete_browser_token", side_effect=delayed_decision
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                removal = executor.submit(self.mutate, selected=False)
                self.assertTrue(decision_ready.wait(timeout=2))
                addition = executor.submit(
                    self.mutate,
                    selected=True,
                    event=self.other_event,
                    photo=self.other_photo,
                )
                self.assertFalse(addition.done())
                release_decision.set()
                removed = removal.result()
                added = addition.result()

        self.assertTrue(removed.delete_browser_token)
        self.assertTrue(added.changed)
        self.assertEqual(Cart.objects.filter(event=self.other_event).count(), 1)

    def test_photo_policy_change_waits_for_an_add_and_is_pruned_afterwards(self) -> None:
        item_create_ready = ThreadEvent()
        release_item_create = ThreadEvent()
        policy_change_started = ThreadEvent()
        policy_change_finished = ThreadEvent()
        original_create = CartItem.objects.create

        def delayed_item_create(*args, **kwargs):
            item_create_ready.set()
            self.assertTrue(release_item_create.wait(timeout=2))
            return original_create(*args, **kwargs)

        def currently_eligible(*, event, watermarked_previews_enabled):
            return Photo.objects.filter(
                pk=self.photo.pk,
                event=event,
                gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
            )

        def make_photo_ineligible() -> None:
            close_old_connections()
            try:
                policy_change_started.set()
                Photo.objects.filter(pk=self.photo.pk).update(
                    gallery_media_policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
                    processing_generation=Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1,
                )
                policy_change_finished.set()
            finally:
                close_old_connections()

        with (
            patch(
                "commerce.services.purchasable_paid_photo_queryset",
                side_effect=currently_eligible,
            ),
            patch.object(CartItem.objects, "create", side_effect=delayed_item_create),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            add = executor.submit(self.mutate, selected=True)
            self.assertTrue(item_create_ready.wait(timeout=2))
            change = executor.submit(make_photo_ineligible)
            self.assertTrue(policy_change_started.wait(timeout=2))
            self.assertFalse(policy_change_finished.wait(timeout=0.2))
            release_item_create.set()
            result = add.result()
            change.result()
            pruned = read_cart(
                event=self.event,
                browser_token=self.token,
                watermarked_previews_enabled=True,
            )

        self.assertTrue(result.changed)
        self.assertTrue(pruned.pruned)
        self.assertEqual(pruned.photo_ids, ())
