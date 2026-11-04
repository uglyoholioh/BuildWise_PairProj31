from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Review(models.Model):
    purchased_pc = models.OneToOneField("orders.PurchasedPC", on_delete=models.CASCADE, related_name="review")
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    text = models.TextField()
    build_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(rating__gte=1, rating__lte=5), name="review_rating_between_1_and_5",
        )]


class SupportRequest(models.Model):
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="support_requests")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="support_requests")
    status = models.CharField(max_length=24, default="open")
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SupportMessage(models.Model):
    request = models.ForeignKey(SupportRequest, on_delete=models.CASCADE, related_name="messages")
    sender_type = models.CharField(max_length=16, choices=[("customer", "Customer"), ("employee", "Employee")])
    employee = models.ForeignKey("accounts.Employee", null=True, blank=True, on_delete=models.PROTECT)
    is_staff_only = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
