import secrets

ORDER_NUMBER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_order_public_number() -> str:
    from commerce.models import Order

    while True:
        number = "FM-" + "".join(secrets.choice(ORDER_NUMBER_ALPHABET) for _ in range(8))
        if not Order.objects.filter(public_number=number).exists():
            return number
