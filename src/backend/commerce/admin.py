from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from feature_flags import services as feature_flag_services
from feature_flags.registry import PAID_PHOTO_PURCHASE

from commerce.attention import resolve_attention_manually
from commerce.capabilities import (
    create_order_access_grant,
    revoke_order_access_grant,
    sign_order_access_grant,
)
from commerce.checkout import CheckoutPaymentUnavailable
from commerce.delivery import (
    ResendOrderAccessRateLimited,
    correct_delivery_email,
    resend_order_access,
    retry_failed_email_delivery,
)
from commerce.models import (
    CommerceAttention,
    DownloadGrantAudit,
    EmailDelivery,
    EmailDeliveryAttempt,
    Order,
    OrderAccessGrant,
    OrderItem,
    PaymentAttempt,
    PaymentEvidence,
)
from commerce.payment_gateway import PaymentGatewayError
from commerce.payments import (
    PaymentReconciliationUnavailable,
    PaymentTransitionRejected,
    cancel_order,
    mark_order_paid_manually,
    reconcile_payment_attempt,
)
from commerce.pricing import format_rub


def _rub(amount_kopecks: int) -> str:
    return format_rub(amount_kopecks)


def _new_commerce_side_effects_enabled(request: HttpRequest) -> bool:
    return feature_flag_services.is_enabled(PAID_PHOTO_PURCHASE, request.user)


def _payment_gateway_for_adapter(request: HttpRequest, adapter_key: str):
    """Resolve only the active adapter that owns the immutable attempt identity."""
    from commerce.views import _payment_gateway

    gateway = _payment_gateway(request)
    if getattr(gateway, "adapter_key", None) != adapter_key:
        raise PaymentTransitionRejected("Payment gateway does not own this attempt.")
    return gateway


def _order_access_signing_secret() -> str | bytes:
    secret = getattr(settings, "COMMERCE_ORDER_ACCESS_SIGNING_SECRET", "")
    if not isinstance(secret, (str, bytes)) or not secret:
        raise ValueError("Order access signing is not configured.")
    return secret


def _one_time_access_link(request: HttpRequest, grant: OrderAccessGrant) -> str:
    signature = sign_order_access_grant(
        grant=grant,
        signing_secret=_order_access_signing_secret(),
    )
    path_value = reverse(
        "commerce:grant_order",
        kwargs={
            "public_number": grant.order.public_number,
            "grant_identifier": grant.pk,
            "signature": signature,
        },
    )
    return request.build_absolute_uri(path_value)


def _one_time_access_link_response(request: HttpRequest, grant: OrderAccessGrant) -> HttpResponse:
    response = HttpResponse(
        "Секретная ссылка для заказа "
        f"{grant.order.public_number}:\n{_one_time_access_link(request, grant)}\n",
        content_type="text/plain; charset=utf-8",
    )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


class OrderItemInline(ReadOnlyInline):
    model = OrderItem
    fields = ("photo", "photo_public_id", "unit_price_display", "quantity", "line_total_display")
    readonly_fields = fields

    @admin.display(description="Цена за фото")
    def unit_price_display(self, item: OrderItem) -> str:
        return _rub(item.unit_price_kopecks)

    @admin.display(description="Сумма строки")
    def line_total_display(self, item: OrderItem) -> str:
        return _rub(item.line_total_kopecks)


class PaymentAttemptInline(ReadOnlyInline):
    model = PaymentAttempt
    fields = (
        "adapter_key",
        "provider_payment_id",
        "amount_display",
        "currency",
        "idempotency_key",
        "confirmation_url",
        "expires_at",
        "status",
        "terminal_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    @admin.display(description="Сумма платежа")
    def amount_display(self, attempt: PaymentAttempt) -> str:
        return _rub(attempt.amount_kopecks)


class EmailDeliveryInline(ReadOnlyInline):
    model = EmailDelivery
    fields = (
        "message_kind",
        "recipient_email",
        "access_grant",
        "state",
        "attempt_count",
        "next_attempt_at",
        "last_failure_category",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields


class OrderAccessGrantInline(ReadOnlyInline):
    model = OrderAccessGrant
    fields = ("id", "source", "created_by", "created_at", "revoked_at")
    readonly_fields = fields


class CommerceAttentionInline(ReadOnlyInline):
    model = CommerceAttention
    fields = (
        "kind",
        "subject",
        "payment_attempt",
        "first_observed_at",
        "last_observed_at",
        "resolved_at",
        "resolution_source",
        "resolution_comment",
    )
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("public_number", "event", "status", "total_display", "currency", "created_at")
    list_filter = ("status", "currency", "event")
    search_fields = ("public_number", "checkout_email", "delivery_email")
    ordering = ("-created_at",)
    fields = (
        "public_number",
        "event",
        "checkout_email",
        "delivery_email",
        "total_display",
        "currency",
        "status",
        "paid_at",
        "first_customer_access_at",
        "created_at",
        "payment_evidence",
        "download_grant_audit",
    )
    readonly_fields = (
        "public_number",
        "event",
        "checkout_email",
        "total_display",
        "currency",
        "status",
        "paid_at",
        "first_customer_access_at",
        "created_at",
        "payment_evidence",
        "download_grant_audit",
    )
    inlines = (
        OrderItemInline,
        PaymentAttemptInline,
        EmailDeliveryInline,
        OrderAccessGrantInline,
        CommerceAttentionInline,
    )
    actions = (
        "confirm_payment_manually",
        "cancel_pending_orders",
        "resend_order_access",
        "create_order_access_grant",
    )

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def save_model(self, request, obj, form, change) -> None:  # noqa: ARG002
        if change and "delivery_email" in form.changed_data:
            correct_delivery_email(order_id=obj.pk, delivery_email=obj.delivery_email)
            obj.refresh_from_db()
            return
        super().save_model(request, obj, form, change)

    @admin.display(description="Сумма заказа", ordering="total_kopecks")
    def total_display(self, order: Order) -> str:
        return _rub(order.total_kopecks)

    @admin.display(description="Нормализованная платежная история")
    def payment_evidence(self, order: Order) -> str:
        values = PaymentEvidence.objects.filter(payment_attempt__order=order).values_list(
            "source",
            "provider_event_id",
            "normalized_status",
            "amount_kopecks",
            "currency",
            "observed_at",
        )
        return (
            "\n".join(
                f"{source}: {provider_event_id or '—'}; {status}; {_rub(amount)}; {currency}; "
                f"{observed_at:%F %T}"
                for source, provider_event_id, status, amount, currency, observed_at in values
            )
            or "—"
        )

    @admin.display(description="Аудит выдачи оригиналов")
    def download_grant_audit(self, order: Order) -> str:
        values = DownloadGrantAudit.objects.filter(order_item__order=order).values_list(
            "order_item__photo_public_id",
            "authorization_source",
            "access_grant_id",
            "created_at",
        )
        return (
            "\n".join(
                f"{photo_id}: {source}; grant {grant_id or '—'}; {created_at:%F %T}"
                for photo_id, source, grant_id, created_at in values
            )
            or "—"
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("commerce.change_order"):
            for action in self.actions:
                actions.pop(action, None)
            return actions
        if not _new_commerce_side_effects_enabled(request):
            for action in (
                "confirm_payment_manually",
                "resend_order_access",
                "create_order_access_grant",
            ):
                actions.pop(action, None)
        return actions

    @admin.action(description="Подтвердить оплату вручную")
    def confirm_payment_manually(self, request: HttpRequest, queryset):
        if not _new_commerce_side_effects_enabled(request):
            self.message_user(request, "Ручное подтверждение оплаты отключено.", messages.ERROR)
            return None
        if request.POST.get("confirm_manual_paid") != "yes":
            context = {
                **self.admin_site.each_context(request),
                "title": "Подтвердить оплату вручную",
                "queryset": queryset,
                "action_name": "confirm_payment_manually",
            }
            return TemplateResponse(
                request,
                "admin/commerce/order/manual_paid_confirmation.html",
                context,
            )
        changed = 0
        for order in queryset:
            try:
                mark_order_paid_manually(order_id=order.pk, actor=request.user)
            except PaymentTransitionRejected:
                continue
            changed += 1
        self.message_user(request, f"Ручное подтверждение: {changed}.")
        return None

    @admin.action(description="Отменить ожидающие заказы")
    def cancel_pending_orders(self, request: HttpRequest, queryset) -> None:
        changed = 0
        for order in queryset:
            try:
                cancel_order(order_id=order.pk, actor=request.user)
            except PaymentTransitionRejected:
                continue
            changed += 1
        self.message_user(request, f"Отменено заказов: {changed}.")

    @admin.action(description="Отправить доступ ещё раз")
    def resend_order_access(self, request: HttpRequest, queryset) -> None:
        if not _new_commerce_side_effects_enabled(request):
            self.message_user(request, "Повторная отправка отключена.", messages.ERROR)
            return None
        changed = 0
        for order in queryset:
            try:
                resend_order_access(order_id=order.pk)
            except (ResendOrderAccessRateLimited, ValueError):
                continue
            self.log_change(request, order, "Доступ отправлен повторно.")
            changed += 1
        self.message_user(request, f"Создано повторных отправок: {changed}.")

    @admin.action(description="Создать новую секретную ссылку")
    def create_order_access_grant(self, request: HttpRequest, queryset):
        if not _new_commerce_side_effects_enabled(request):
            self.message_user(request, "Создание новой ссылки отключено.", messages.ERROR)
            return None
        order = _exactly_one(queryset)
        if order is None:
            self.message_user(request, "Выберите ровно один заказ.", messages.ERROR)
            return None
        try:
            _order_access_signing_secret()
        except ValueError:
            self.message_user(request, "Подписание ссылок не настроено.", messages.ERROR)
            return None
        grant = create_order_access_grant(
            order=order,
            source=str(OrderAccessGrant.Source.ADMIN),
            created_by=request.user,
        )
        self.log_change(request, order, "Создана новая секретная ссылка.")
        return _one_time_access_link_response(request, grant)


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    change_form_template = "admin/commerce/paymentattempt/change_form.html"
    list_display = (
        "id",
        "order",
        "adapter_key",
        "status",
        "amount_display",
        "currency",
        "updated_at",
    )
    list_filter = ("status", "adapter_key", "currency")
    search_fields = ("order__public_number", "provider_payment_id", "idempotency_key")
    fields = (
        "order",
        "amount_display",
        "currency",
        "adapter_key",
        "idempotency_key",
        "provider_payment_id",
        "confirmation_url",
        "expires_at",
        "status",
        "terminal_at",
        "reconciliation_state",
        "reconciliation_next_attempt_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    @admin.display(description="Сумма платежа", ordering="amount_kopecks")
    def amount_display(self, attempt: PaymentAttempt) -> str:
        return _rub(attempt.amount_kopecks)

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def get_urls(self):
        info = self.opts.app_label, self.opts.model_name
        return [
            path(
                "<path:object_id>/refresh/",
                self.admin_site.admin_view(self.refresh),
                name=f"{info[0]}_{info[1]}_refresh",
            )
        ] + super().get_urls()

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url="",
        obj=None,
    ):
        if obj is not None and self._can_refresh(request):
            context["can_refresh_payment_attempt"] = True
            context["payment_attempt_refresh_url"] = reverse(
                "admin:commerce_paymentattempt_refresh",
                args=(obj.pk,),
            )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def _can_refresh(self, request: HttpRequest) -> bool:
        return (
            request.user.has_perm("commerce.view_paymentattempt")
            and request.user.has_perm("commerce.change_order")
            and _new_commerce_side_effects_enabled(request)
        )

    def refresh(self, request: HttpRequest, object_id: str) -> HttpResponse:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        attempt = self.get_object(request, object_id)
        if attempt is None or not self._can_refresh(request):
            raise PermissionDenied
        try:
            gateway = _payment_gateway_for_adapter(request, attempt.adapter_key)
            reconcile_payment_attempt(attempt_id=attempt.pk, gateway=gateway)
        except (
            CheckoutPaymentUnavailable,
            PaymentGatewayError,
            PaymentReconciliationUnavailable,
            PaymentTransitionRejected,
            ValueError,
        ):
            self.message_user(
                request, "Не удалось безопасно проверить статус в банке.", messages.ERROR
            )
        else:
            self.log_change(request, attempt, "Статус в банке проверен.")
            self.message_user(request, "Статус в банке проверен.")
        return HttpResponseRedirect(
            reverse("admin:commerce_paymentattempt_change", args=(attempt.pk,))
        )


@admin.register(PaymentEvidence)
class PaymentEvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "payment_attempt",
        "source",
        "normalized_status",
        "amount_display",
        "currency",
        "observed_at",
    )
    list_filter = ("source", "normalized_status", "currency")
    search_fields = ("payment_attempt__order__public_number", "provider_event_id")
    fields = (
        "payment_attempt",
        "source",
        "provider_event_id",
        "normalized_status",
        "amount_display",
        "currency",
        "observed_at",
        "created_at",
    )
    readonly_fields = fields

    @admin.display(description="Сумма подтверждения", ordering="amount_kopecks")
    def amount_display(self, evidence: PaymentEvidence) -> str:
        return _rub(evidence.amount_kopecks)

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


@admin.register(OrderAccessGrant)
class OrderAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "source", "created_by", "created_at", "revoked_at")
    list_filter = ("source", "revoked_at")
    search_fields = ("order__public_number",)
    fields = ("id", "order", "source", "created_by", "created_at", "revoked_at")
    readonly_fields = fields
    actions = ("copy_order_access_grant", "revoke_order_access_grant")

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("commerce.change_order"):
            actions.pop("copy_order_access_grant", None)
            actions.pop("revoke_order_access_grant", None)
        elif not _new_commerce_side_effects_enabled(request):
            actions.pop("copy_order_access_grant", None)
        return actions

    @admin.action(description="Скопировать секретную ссылку")
    def copy_order_access_grant(self, request: HttpRequest, queryset):
        if not _new_commerce_side_effects_enabled(request):
            self.message_user(request, "Копирование секретной ссылки отключено.", messages.ERROR)
            return None
        grant = _exactly_one(queryset.filter(revoked_at__isnull=True))
        if grant is None:
            self.message_user(request, "Выберите ровно одну активную ссылку.", messages.ERROR)
            return None
        try:
            _order_access_signing_secret()
        except ValueError:
            self.message_user(request, "Подписание ссылок не настроено.", messages.ERROR)
            return None
        self.log_change(request, grant, "Секретная ссылка показана однократно.")
        return _one_time_access_link_response(request, grant)

    @admin.action(description="Отозвать секретные ссылки")
    def revoke_order_access_grant(self, request: HttpRequest, queryset) -> None:
        changed = 0
        for grant in queryset:
            revoked_grant = revoke_order_access_grant(grant)
            if revoked_grant is None:
                continue
            self.log_change(request, revoked_grant, "Секретная ссылка отозвана.")
            changed += 1
        self.message_user(request, f"Отозвано ссылок: {changed}.")


@admin.register(EmailDelivery)
class EmailDeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "recipient_email", "state", "attempt_count", "next_attempt_at")
    list_filter = ("state", "message_kind")
    search_fields = ("order__public_number", "recipient_email")
    fields = (
        "order",
        "message_kind",
        "recipient_email",
        "access_grant",
        "state",
        "attempt_count",
        "next_attempt_at",
        "last_failure_category",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields
    actions = ("retry_failed_delivery",)

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not _new_commerce_side_effects_enabled(request) or not request.user.has_perm(
            "commerce.change_order"
        ):
            actions.pop("retry_failed_delivery", None)
        return actions

    @admin.action(description="Повторить неудачную отправку")
    def retry_failed_delivery(self, request: HttpRequest, queryset) -> None:
        if not _new_commerce_side_effects_enabled(request):
            self.message_user(request, "Повторная отправка отключена.", messages.ERROR)
            return None
        changed = 0
        for delivery_id in queryset.values_list("pk", flat=True):
            delivery = retry_failed_email_delivery(delivery_id=delivery_id)
            if delivery is None:
                continue
            self.log_change(request, delivery, "Неудачная отправка поставлена на повтор.")
            changed += 1
        self.message_user(request, f"Повторных отправок: {changed}.")


@admin.register(EmailDeliveryAttempt)
class EmailDeliveryAttemptAdmin(admin.ModelAdmin):
    list_display = ("delivery", "attempt_number", "recipient_email", "outcome", "attempted_at")
    list_filter = ("outcome",)
    search_fields = ("delivery__order__public_number", "recipient_email")
    fields = (
        "delivery",
        "attempt_number",
        "recipient_email",
        "outcome",
        "safe_failure_category",
        "attempted_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


@admin.register(DownloadGrantAudit)
class DownloadGrantAuditAdmin(admin.ModelAdmin):
    list_display = ("order_item", "authorization_source", "access_grant", "created_at")
    list_filter = ("authorization_source",)
    search_fields = ("order_item__order__public_number", "order_item__photo_public_id")
    fields = ("order_item", "authorization_source", "access_grant", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


@admin.register(CommerceAttention)
class CommerceAttentionAdmin(admin.ModelAdmin):
    change_list_template = "admin/commerce/commerceattention/change_list.html"
    list_display = (
        "kind",
        "subject",
        "order",
        "payment_attempt",
        "last_observed_at",
        "resolved_at",
        "resolution_source",
    )
    list_filter = ("kind", "resolution_source", "resolved_at")
    search_fields = ("subject", "order__public_number")
    fields = (
        "kind",
        "subject",
        "order",
        "payment_attempt",
        "first_observed_at",
        "last_observed_at",
        "next_reminder_at",
        "resolved_at",
        "resolution_source",
        "resolution_comment",
    )
    readonly_fields = fields
    actions = ("resolve_attention",)

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def changelist_view(self, request, extra_context=None):
        context = {
            "open_attention_count": CommerceAttention.objects.filter(
                resolved_at__isnull=True
            ).count()
        }
        if extra_context:
            context.update(extra_context)
        return super().changelist_view(request, extra_context=context)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("commerce.handle_attention"):
            actions.pop("resolve_attention", None)
        return actions

    @admin.action(description="Разрешить обращение")
    def resolve_attention(self, request: HttpRequest, queryset) -> None:
        if not request.user.has_perm("commerce.handle_attention"):
            raise PermissionDenied
        comment = request.POST.get("resolution_comment", "")
        if request.POST.get("confirm_attention_resolution") != "yes" or not comment.strip():
            context = {
                **self.admin_site.each_context(request),
                "title": "Разрешить обращение",
                "queryset": queryset,
                "action_name": "resolve_attention",
                "resolution_comment": comment,
                "comment_error": request.POST.get("confirm_attention_resolution") == "yes",
            }
            return TemplateResponse(
                request,
                "admin/commerce/commerceattention/resolve_confirmation.html",
                context,
            )
        changed = 0
        for attention in queryset:
            resolution = resolve_attention_manually(attention_id=attention.pk, comment=comment)
            if resolution.performed:
                self.log_change(request, resolution.attention, "Обращение разрешено вручную.")
                changed += 1
        self.message_user(request, f"Разрешено обращений: {changed}.")


def _exactly_one(queryset):
    objects = list(queryset[:2])
    return objects[0] if len(objects) == 1 else None
