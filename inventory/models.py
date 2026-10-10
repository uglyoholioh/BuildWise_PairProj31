from django.conf import settings
from django.db import models


class StockAdjustment(models.Model):
    item_type = models.CharField(max_length=16, choices=[("component", "Component"), ("accessory", "Accessory")])
    component = models.ForeignKey("catalogue.Component", null=True, blank=True, on_delete=models.PROTECT)
    accessory = models.ForeignKey("catalogue.Accessory", null=True, blank=True, on_delete=models.PROTECT)
    delta = models.IntegerField()
    reason = models.CharField(max_length=240)
    created_by = models.ForeignKey("accounts.Employee", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class Reservation(models.Model):
    class Status(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        CONSUMED = "consumed", "Consumed"
        RELEASED = "released", "Released"

    component = models.ForeignKey("catalogue.Component", null=True, blank=True, on_delete=models.PROTECT)
    accessory = models.ForeignKey("catalogue.Accessory", null=True, blank=True, on_delete=models.PROTECT)
    purchased_pc = models.ForeignKey("orders.PurchasedPC", null=True, blank=True, on_delete=models.PROTECT, related_name="reservations")
    order_line = models.ForeignKey("orders.OrderLine", null=True, blank=True, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RESERVED)
