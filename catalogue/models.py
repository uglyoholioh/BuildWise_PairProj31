from django.conf import settings
from django.db import models


def specification_fields(specs):
    # Older component records and order snapshots can contain non-object JSON.
    return specs if isinstance(specs, dict) else {}


class Component(models.Model):
    class Category(models.TextChoices):
        CPU = "cpu", "CPU"
        MOTHERBOARD = "motherboard", "Motherboard"
        MEMORY = "memory", "Memory"
        GPU = "gpu", "Graphics card"
        STORAGE = "storage", "Storage"
        POWER_SUPPLY = "power_supply", "Power supply"
        CASE = "case", "Case"
        COOLER = "cooler", "CPU cooler"

    category = models.CharField(max_length=40, choices=Category.choices)
    brand = models.CharField(max_length=100)
    model = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    image = models.URLField(max_length=1000, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    specs = models.JSONField(default=dict, blank=True)
    socket = models.CharField(max_length=40, blank=True)
    memory_type = models.CharField(max_length=40, blank=True)
    form_factor = models.CharField(max_length=40, blank=True)
    ml_tier = models.CharField(max_length=40, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(price__gt=0), name="component_price_positive")]

    def __str__(self):
        return f"{self.brand} {self.model}"


class ComponentConflict(models.Model):
    component_a = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="conflicts_as_a"
    )
    component_b = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="conflicts_as_b"
    )
    reason = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("component_a", "component_b"), name="unique_component_conflict_pair"
            )
        ]

    def save(self, *args, **kwargs):
        if self.component_a_id and self.component_b_id and self.component_a_id > self.component_b_id:
            self.component_a_id, self.component_b_id = self.component_b_id, self.component_a_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.component_a} / {self.component_b}"


class Configuration(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        WITHDRAWN = "withdrawn", "Withdrawn"

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    image = models.CharField(max_length=255, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    components = models.ManyToManyField(Component, through="ConfigurationItem")

    def __str__(self):
        return self.name


class ConfigurationItem(models.Model):
    configuration = models.ForeignKey(Configuration, on_delete=models.CASCADE, related_name="items")
    component = models.ForeignKey(Component, on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("configuration", "component"), name="unique_component_per_configuration"
            ),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="configuration_quantity_positive"),
        ]


class Accessory(models.Model):
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.URLField(max_length=1000, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(price__gt=0), name="accessory_price_positive")]

    def __str__(self):
        return self.name


class AssemblyFeeSetting(models.Model):
    fee_amount = models.DecimalField(max_digits=10, decimal_places=2)
    updated_by = models.ForeignKey("accounts.Employee", on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(fee_amount__gte=0), name="assembly_fee_nonnegative")]
