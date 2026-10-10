from django.conf import settings
from django.db import models


class Component(models.Model):
    category = models.CharField(max_length=40)
    brand = models.CharField(max_length=100)
    model = models.CharField(max_length=120)
    specs = models.JSONField(default=dict, blank=True)
    socket = models.CharField(max_length=40, blank=True)
    memory_type = models.CharField(max_length=40, blank=True)
    form_factor = models.CharField(max_length=40, blank=True)
    ml_tier = models.CharField(max_length=40, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.brand} {self.model}"


class Configuration(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        WITHDRAWN = "withdrawn", "Withdrawn"

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    image = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    components = models.ManyToManyField(Component, through="ConfigurationItem")

    def __str__(self):
        return self.name


class ConfigurationItem(models.Model):
    configuration = models.ForeignKey(Configuration, on_delete=models.CASCADE, related_name="items")
    component = models.ForeignKey(Component, on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField(default=1)


class Accessory(models.Model):
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class AssemblyFeeSetting(models.Model):
    fee_amount = models.DecimalField(max_digits=10, decimal_places=2)
    updated_by = models.ForeignKey("accounts.Employee", on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now_add=True)
