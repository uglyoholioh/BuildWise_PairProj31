from django.conf import settings
from django.db import models


class Review(models.Model):
    purchased_pc = models.OneToOneField("orders.PurchasedPC", on_delete=models.CASCADE, related_name="review")
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="reviews")
    rating = models.PositiveSmallIntegerField()
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class SupportRequest(models.Model):
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="support_requests")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="support_requests")
    status = models.CharField(max_length=24, default="open")
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SupportMessage(models.Model):
    request = models.ForeignKey(SupportRequest, on_delete=models.CASCADE, related_name="messages")
    sender_type = models.CharField(max_length=16, choices=[("customer", "Customer"), ("employee", "Employee")])
    is_staff_only = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
