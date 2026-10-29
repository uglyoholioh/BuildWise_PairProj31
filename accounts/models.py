from django.contrib.auth.models import User
from django.db import models
from django.db.models.functions import Lower


class Customer(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="customer_profile"
    )
    name = models.CharField(max_length=160)
    email = models.EmailField(unique=True)
    delivery_address = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="customer_email_case_insensitive_unique")]

    def __str__(self):
        return self.name


class Employee(models.Model):
    class Role(models.TextChoices):
        EMPLOYEE = "employee", "Employee"
        MANAGER = "manager", "Manager"

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="employee_profile"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.EMPLOYEE)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.user.get_username()


class StaffAction(models.Model):
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="actions"
    )
    action_type = models.CharField(max_length=80)
    object_ref = models.CharField(max_length=160, blank=True)
    detail = models.TextField(blank=True)
    acted_at = models.DateTimeField(auto_now_add=True)
