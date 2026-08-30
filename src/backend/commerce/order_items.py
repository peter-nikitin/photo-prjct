from django.core.paginator import Page, Paginator

from commerce.models import Order, OrderItem

ORDER_ITEM_PAGE_SIZE = 100


def order_item_page(*, order: Order, page_number: object) -> Page[OrderItem]:
    """Resolve one stable page of immutable items for rendering and delivery."""
    queryset = OrderItem.objects.select_related("photo").filter(order=order).order_by("photo_id")
    return Paginator(queryset, ORDER_ITEM_PAGE_SIZE).page(1 if page_number is None else page_number)
