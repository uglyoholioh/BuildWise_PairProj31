from decimal import Decimal

from inventory.availability import available_component_quantity
from .models import AssemblyFeeSetting


def calculate_pc_price(items, assembly_fee):
    return sum(
        (item.component.price * item.quantity for item in items), Decimal("0.00")
    ) + assembly_fee


def available_preset_quantity(items):
    items = list(items)
    if not items:
        return 0
    return min(
        available_component_quantity(item.component) // item.quantity for item in items
    )


def current_assembly_fee():
    return AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()
