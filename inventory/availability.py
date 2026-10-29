from django.db.models import Sum

from .models import Reservation, StockAdjustment


def stock_position(item_type, item):
    field = "component" if item_type == "component" else "accessory"
    item_filter = {field: item}
    physical = StockAdjustment.objects.filter(
        item_type=item_type, **item_filter
    ).aggregate(total=Sum("delta"))["total"] or 0
    reserved = Reservation.objects.filter(
        status=Reservation.Status.RESERVED, **item_filter
    ).aggregate(total=Sum("quantity"))["total"] or 0
    return {"physical": physical, "reserved": reserved, "available": max(0, physical - reserved)}


def available_component_quantity(component):
    return stock_position("component", component)["available"]


def available_accessory_quantity(accessory):
    return stock_position("accessory", accessory)["available"]
