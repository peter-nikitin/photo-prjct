from decimal import Decimal

from django.contrib import admin
from django.forms import BaseInlineFormSet, DecimalField, ModelForm

from picflow.models import Event, EventFolder, Photo


class EventFolderInlineFormSet(BaseInlineFormSet):
    def clean(self) -> None:
        super().clean()
        names: dict[str, list] = {}
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            name = form.cleaned_data.get("name")
            if name:
                names.setdefault(name.strip().casefold(), []).append(form)

        for forms in names.values():
            if len(forms) > 1:
                for form in forms:
                    form.add_error("name", "Folder names must be unique within an event.")


class EventFolderInline(admin.TabularInline):
    model = EventFolder
    formset = EventFolderInlineFormSet
    extra = 1


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    class Form(ModelForm):
        price_per_photo_rub = DecimalField(
            label="Цена фотографии, ₽",
            required=False,
            max_digits=12,
            decimal_places=2,
            min_value=Decimal("0.01"),
        )

        class Meta:
            model = Event
            fields = (
                "name",
                "slug",
                "description",
                "cover",
                "start_date",
                "end_date",
                "city",
                "timezone_name",
                "access_type",
                "price_per_photo_rub",
                "publication_status",
            )

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            if not self.is_bound and self.instance._state.adding:
                self.initial.setdefault("timezone_name", "Europe/Moscow")
            if self.instance.price_per_photo_kopecks is not None:
                self.initial.setdefault(
                    "price_per_photo_rub",
                    Decimal(self.instance.price_per_photo_kopecks) / Decimal(100),
                )

        def clean(self):
            cleaned_data = super().clean()
            access_type = cleaned_data.get("access_type")
            price_rub = cleaned_data.get("price_per_photo_rub")
            if price_rub is None:
                self.instance.price_per_photo_kopecks = None
            else:
                self.instance.price_per_photo_kopecks = int(price_rub * Decimal(100))

            if access_type == Event.AccessType.FREE and price_rub is not None:
                self.add_error("price_per_photo_rub", "Free events cannot have a photo price.")
            elif access_type == Event.AccessType.PAID and price_rub is None:
                self.add_error(
                    "price_per_photo_rub",
                    "Paid events require a positive photo price.",
                )

            if (
                not self.instance.pk
                or access_type is None
                or "access_type" not in self.changed_data
            ):
                return cleaned_data

            persisted = Event.objects.select_for_update().get(pk=self.instance.pk)
            if access_type != persisted.access_type and persisted.photos.exists():
                self.add_error(
                    "access_type",
                    "Access type cannot be changed after the event has photos.",
                )
            return cleaned_data

    form = Form
    inlines = (EventFolderInline,)
    list_display = (
        "name",
        "start_date",
        "end_date",
        "city",
        "access_type",
        "publication_status",
    )
    list_filter = ("publication_status", "access_type", "city")
    search_fields = ("name", "city", "description")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        ("Content", {"fields": ("name", "slug", "description", "cover")}),
        ("Schedule", {"fields": ("start_date", "end_date", "city", "timezone_name")}),
        ("Access and publication", {"fields": ("access_type", "publication_status")}),
        ("Commerce", {"fields": ("price_per_photo_rub",)}),
    )

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    class Form(ModelForm):
        class Meta:
            model = Photo
            fields = (
                "id",
                "event",
                "src",
                "uploaded_by",
                "original_key",
                "original_filename",
                "original_size",
                "original_content_type",
                "uploaded_at",
                "processing_generation",
                "gallery_media_policy",
            )

        def clean(self):
            cleaned_data = super().clean()
            if self.instance.pk:
                persisted = (
                    Photo.objects.select_for_update()
                    .select_related("event")
                    .get(pk=self.instance.pk)
                )
                immutable_values = {
                    "event": persisted.event,
                    "processing_generation": persisted.processing_generation,
                    "gallery_media_policy": persisted.gallery_media_policy,
                }
                immutable_errors = {
                    "event": "Event cannot be changed after the photo has been created.",
                    "processing_generation": (
                        "Processing generation cannot be changed after the photo has been created."
                    ),
                    "gallery_media_policy": (
                        "Gallery media policy cannot be changed after the photo has been created."
                    ),
                }
                if persisted.order_items.filter(order__status="paid").exists():
                    immutable_values.update(
                        {
                            "original_key": persisted.original_key,
                            "original_content_type": persisted.original_content_type,
                        }
                    )
                    immutable_errors.update(
                        {
                            "original_key": (
                                "Original key cannot be changed after the photo has a paid "
                                "order item."
                            ),
                            "original_content_type": (
                                "Original content type cannot be changed after the photo has "
                                "a paid order item."
                            ),
                        }
                    )
                for field, persisted_value in immutable_values.items():
                    if field in cleaned_data and cleaned_data[field] != persisted_value:
                        self.add_error(field, immutable_errors[field])

            event = cleaned_data.get("event")
            if event and self.instance.folder_id and self.instance.folder.event_id != event.pk:
                self.add_error(
                    "event", "A photo with a folder can only belong to that folder's event."
                )
            return cleaned_data

    form = Form
    list_display = ("id", "event", "src")
    list_filter = ("event",)
    search_fields = ("id",)
