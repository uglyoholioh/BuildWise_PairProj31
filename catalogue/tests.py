from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer, Employee, StaffAction
from .forms import ComponentForm
from .models import Component


class ComponentFormTests(TestCase):
    def data(self, **overrides):
        return {
            "category": Component.Category.CPU,
            "brand": "Example",
            "model": "X1",
            "description": "Test component",
            "price": "100.00",
            "active": "on",
            "socket": "AM5",
            "memory_type": "",
            "form_factor": "",
            "ml_tier": "",
            "specs": "{}",
            **overrides,
        }

    def test_cpu_requires_socket(self):
        form = ComponentForm(self.data(socket=""))
        self.assertFalse(form.is_valid())
        self.assertIn("socket", form.errors)

    def test_price_must_be_positive(self):
        form = ComponentForm(self.data(price="0"))
        self.assertFalse(form.is_valid())
        self.assertIn("price", form.errors)


class ComponentManagementTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="staff", password="safe-pass-123")
        Employee.objects.create(user=user)
        self.client.login(username="staff", password="safe-pass-123")

    def component_data(self, **overrides):
        return {
            "category": Component.Category.CPU,
            "brand": "Example",
            "model": "X1",
            "description": "Test component",
            "price": "100.00",
            "active": "on",
            "socket": "AM5",
            "memory_type": "",
            "form_factor": "",
            "ml_tier": "",
            "specs": "{}",
            **overrides,
        }

    def test_staff_can_create_and_deactivate_component_with_audit(self):
        response = self.client.post(
            reverse("catalogue:component_create"), self.component_data()
        )
        self.assertRedirects(response, reverse("catalogue:component_list"))
        component = Component.objects.get()
        self.assertTrue(component.active)
        self.assertEqual(StaffAction.objects.get().action_type, "component_created")

        response = self.client.post(
            reverse("catalogue:component_update", args=[component.pk]),
            self.component_data(active=""),
        )
        self.assertRedirects(response, reverse("catalogue:component_list"))
        component.refresh_from_db()
        self.assertFalse(component.active)
        self.assertEqual(StaffAction.objects.count(), 2)
        self.assertEqual(StaffAction.objects.latest("acted_at").action_type, "component_deactivated")

    def test_customer_cannot_access_staff_catalogue(self):
        self.client.logout()
        user = User.objects.create_user(username="customer", password="safe-pass-123")
        Customer.objects.create(user=user, name="Customer", email="customer@example.com")
        self.client.login(username="customer", password="safe-pass-123")
        response = self.client.get(reverse("catalogue:component_list"))
        self.assertRedirects(response, reverse("staff_login") + "?next=" + reverse("catalogue:component_list"))
