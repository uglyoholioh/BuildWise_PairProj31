from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer, Employee, StaffAction
from .forms import ComponentForm
from .compatibility import check_compatibility
from .models import Component, ComponentConflict, Configuration


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


class CompatibilityCheckerTests(TestCase):
    def component(self, category, model, **specs):
        return Component.objects.create(
            category=category,
            brand="Example",
            model=model,
            price="100.00",
            **specs,
        )

    def test_reports_missing_specs_and_technical_mismatches(self):
        cpu = self.component(Component.Category.CPU, "CPU", socket="AM5")
        board = self.component(
            Component.Category.MOTHERBOARD,
            "Board",
            socket="LGA1700",
            memory_type="DDR5",
            form_factor="ATX",
        )
        memory = self.component(Component.Category.MEMORY, "RAM", memory_type="DDR4")
        case = self.component(Component.Category.CASE, "Case", form_factor="mATX")

        problems = check_compatibility([cpu, board, memory, case])
        self.assertTrue(any("socket" in problem for problem in problems))
        self.assertTrue(any("memory type" in problem for problem in problems))
        self.assertTrue(any("form factor" in problem for problem in problems))

        cpu.socket = ""
        cpu.save(update_fields=("socket",))
        self.assertTrue(any("missing socket" in problem for problem in check_compatibility([cpu])))

    def test_explicit_conflict_matches_canonical_pair(self):
        cpu = self.component(Component.Category.CPU, "CPU", socket="AM5")
        gpu = self.component(Component.Category.GPU, "GPU")
        conflict = ComponentConflict.objects.create(
            component_a=gpu, component_b=cpu, reason="Known clearance issue."
        )

        self.assertLess(conflict.component_a_id, conflict.component_b_id)
        self.assertTrue(any("Known clearance issue" in problem for problem in check_compatibility([cpu, gpu])))

    def test_case_can_support_multiple_motherboard_form_factors(self):
        board = self.component(
            Component.Category.MOTHERBOARD,
            "Board",
            socket="AM5",
            memory_type="DDR5",
            form_factor="mATX",
        )
        case = self.component(Component.Category.CASE, "Case", form_factor="ATX, mATX")

        self.assertFalse(any("form factor" in problem for problem in check_compatibility([board, case])))

    def test_component_change_demotes_affected_published_preset(self):
        user = User.objects.create_user(username="staff", password="safe-pass-123")
        Employee.objects.create(user=user)
        cpu = self.component(Component.Category.CPU, "CPU", socket="AM5")
        board = self.component(
            Component.Category.MOTHERBOARD,
            "Board",
            socket="AM5",
            memory_type="DDR5",
            form_factor="ATX",
        )
        preset = Configuration.objects.create(
            name="Test preset", status=Configuration.Status.PUBLISHED
        )
        preset.components.add(cpu, board)
        self.client.login(username="staff", password="safe-pass-123")

        response = self.client.post(
            reverse("catalogue:component_update", args=[cpu.pk]),
            {
                "category": Component.Category.CPU,
                "brand": "Example",
                "model": "CPU",
                "description": "",
                "price": "100.00",
                "active": "on",
                "socket": "LGA1700",
                "memory_type": "",
                "form_factor": "",
                "ml_tier": "",
                "specs": "{}",
            },
        )
        self.assertRedirects(response, reverse("catalogue:component_list"))
        preset.refresh_from_db()
        self.assertEqual(preset.status, Configuration.Status.DRAFT)
