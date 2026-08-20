from django.contrib import admin
from django.forms import BaseInlineFormSet, ModelForm

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
                "publication_status",
            )

        def clean(self):
            cleaned_data = super().clean()
            access_type = cleaned_data.get("access_type")
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
