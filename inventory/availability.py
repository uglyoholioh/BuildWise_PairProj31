from django.db.models import Sum

from .models import Reservation, StockAdjustment


def available_component_quantity(component):
    physical = StockAdjustment.objects.filter(component=component).aggregate(total=Sum("delta"))["total"] or 0
    reserved = Reservation.objects.filter(
        component=component, status=Reservation.Status.RESERVED
    ).aggregate(total=Sum("quantity"))["total"] or 0
    return max(0, physical - reserved)
