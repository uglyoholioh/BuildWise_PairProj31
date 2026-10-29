from django.conf import settings
from django.db import models
from decimal import Decimal


class Order(models.Model):
    checkout_reference = models.CharField(max_length=64, null=True, blank=True, unique=True)
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=32, default="placed")
    payment_status = models.CharField(max_length=24, default="pending")
    total_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_name = models.CharField(max_length=160)
    delivery_address = models.TextField()
    delivery_phone = models.CharField(max_length=40, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True)


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=16, choices=[("build", "Build"), ("accessory", "Accessory")])
    name = models.CharField(max_length=200)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price_snapshot = models.DecimalField(max_digits=10, decimal_places=2)
    custom_snapshot = models.JSONField(default=dict, blank=True)
    configuration = models.ForeignKey("catalogue.Configuration", null=True, blank=True, on_delete=models.SET_NULL)
    accessory = models.ForeignKey("catalogue.Accessory", null=True, blank=True, on_delete=models.SET_NULL)


class PurchasedPC(models.Model):
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name="pcs")
    unit_index = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=24, default="queued")
    hold_reason = models.TextField(blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    @property
    def approved_parts(self):
        from .allocations import approved_pc_parts
        return approved_pc_parts(self)


class QualityCheck(models.Model):
    purchased_pc = models.ForeignKey(PurchasedPC, on_delete=models.CASCADE, related_name="quality_checks")
    employee = models.ForeignKey("accounts.Employee", on_delete=models.PROTECT)
    stage = models.CharField(max_length=24)
    checklist_item = models.CharField(max_length=160)
    passed = models.BooleanField()
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Shipment(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="shipment")
    status = models.CharField(max_length=16, default="packed")
    courier = models.CharField(max_length=100, blank=True)
    tracking_ref = models.CharField(max_length=120, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.CharField(max_length=16, blank=True)
    note = models.TextField(blank=True)


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=32, blank=True)
    to_status = models.CharField(max_length=32)
    note = models.TextField(blank=True)
    employee = models.ForeignKey("accounts.Employee", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class SubstitutionProposal(models.Model):
    purchased_pc = models.ForeignKey(PurchasedPC, on_delete=models.CASCADE, related_name="substitutions")
    original_component = models.ForeignKey("catalogue.Component", on_delete=models.PROTECT, related_name="original_substitutions")
    replacement_component = models.ForeignKey("catalogue.Component", on_delete=models.PROTECT, related_name="replacement_substitutions")
    proposed_by = models.ForeignKey("accounts.Employee", on_delete=models.PROTECT)
    reason = models.TextField()
    spec_differences = models.TextField(blank=True)
    fps_note = models.TextField(blank=True)
    status = models.CharField(max_length=16, default="pending")
    original_snapshot = models.JSONField(default=dict, blank=True)
    replacement_snapshot = models.JSONField(default=dict, blank=True)
    previous_pc_status = models.CharField(max_length=24, default="queued")
    proposed_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=32, blank=True)
