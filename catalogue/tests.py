from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from decimal import Decimal

from accounts.models import Customer, Employee, StaffAction
from inventory.models import Reservation, StockAdjustment
from orders.models import Order, OrderLine
from .compatibility import check_compatibility
from .forms import ComponentForm
from .models import Accessory, AssemblyFeeSetting, Component, ComponentConflict, Configuration
from .pricing import available_preset_quantity, calculate_pc_price


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


class PresetManagementTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="manager", password="safe-pass-123")
        self.employee = Employee.objects.create(user=user, role=Employee.Role.MANAGER)
        self.client.login(username="manager", password="safe-pass-123")

    def create_components(self):
        specs = {
            Component.Category.CPU: {"socket": "AM5"},
            Component.Category.MOTHERBOARD: {
                "socket": "AM5", "memory_type": "DDR5", "form_factor": "ATX"
            },
            Component.Category.MEMORY: {"memory_type": "DDR5"},
            Component.Category.GPU: {},
            Component.Category.STORAGE: {},
            Component.Category.POWER_SUPPLY: {},
            Component.Category.CASE: {"form_factor": "ATX"},
        }
        return [
            Component.objects.create(
                category=category,
                brand="Example",
                model=category,
                price=Decimal("100.00"),
                **spec,
            )
            for category, spec in specs.items()
        ]

    def preset_data(self, components, status=Configuration.Status.PUBLISHED):
        data = {
            "name": "BuildWise One",
            "description": "A test preset",
            "image": "",
            "status": status,
            "items-TOTAL_FORMS": "8",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "8",
        }
        for index, component in enumerate(components):
            data[f"items-{index}-component"] = str(component.pk)
            data[f"items-{index}-quantity"] = (
                "2" if component.category == Component.Category.MEMORY else "1"
            )
        data["items-7-component"] = ""
        data["items-7-quantity"] = ""
        return data

    def test_publishing_requires_complete_configuration_and_fee(self):
        cpu = self.create_components()[0]
        AssemblyFeeSetting.objects.create(fee_amount="25.00", updated_by=self.employee)

        response = self.client.post(
            reverse("catalogue:preset_create"), self.preset_data([cpu])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Configuration.objects.count(), 0)
        self.assertContains(response, "missing required categories")

    def test_manager_can_publish_compatible_preset_at_shared_price(self):
        components = self.create_components()
        AssemblyFeeSetting.objects.create(fee_amount="25.00", updated_by=self.employee)
        for component in components:
            StockAdjustment.objects.create(
                item_type="component", component=component, delta=4,
                reason="Opening stock", created_by=self.employee,
            )
            Reservation.objects.create(component=component, quantity=1)

        response = self.client.post(
            reverse("catalogue:preset_create"), self.preset_data(components)
        )

        self.assertRedirects(response, reverse("catalogue:preset_manage_list"))
        configuration = Configuration.objects.get()
        self.assertEqual(configuration.status, Configuration.Status.PUBLISHED)
        items = list(configuration.items.select_related("component"))
        self.assertEqual(calculate_pc_price(items, Decimal("25.00")), Decimal("825.00"))
        self.assertEqual(available_preset_quantity(items), 1)
        self.assertContains(self.client.get(reverse("catalogue:preset_manage_list")), "$825.00")

    def test_only_active_manager_can_change_shared_fee(self):
        response = self.client.post(
            reverse("catalogue:assembly_fee"), {"fee_amount": "30.00"}
        )
        self.assertRedirects(response, reverse("catalogue:assembly_fee"))
        self.assertEqual(AssemblyFeeSetting.objects.latest("pk").fee_amount, Decimal("30.00"))

        employee_user = User.objects.create_user(username="employee", password="safe-pass-123")
        Employee.objects.create(user=employee_user)
        self.client.logout()
        self.client.login(username="employee", password="safe-pass-123")
        response = self.client.get(reverse("catalogue:assembly_fee"))
        self.assertEqual(response.status_code, 302)


class AccessoryManagementTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="staff", password="safe-pass-123")
        self.employee = Employee.objects.create(user=user)
        self.client.login(username="staff", password="safe-pass-123")

    def accessory_data(self, **overrides):
        return {"name": "Wireless mouse", "category": "peripheral", "price": "25.00", "active": "on", **overrides}

    def test_staff_can_manage_accessory_and_deactivate_without_changing_old_order(self):
        response = self.client.post(reverse("catalogue:accessory_create"), self.accessory_data())
        self.assertRedirects(response, reverse("catalogue:accessory_manage_list"))
        accessory = Accessory.objects.get()
        customer_user = User.objects.create_user(username="buyer", password="safe-pass-123")
        customer = Customer.objects.create(user=customer_user, name="Buyer", email="buyer@example.com")
        order = Order.objects.create(
            customer=customer,
            total_snapshot=Decimal("25.00"),
            delivery_name="Buyer",
            delivery_address="1 Example Road",
        )
        line = OrderLine.objects.create(
            order=order,
            kind="accessory",
            name=accessory.name,
            quantity=1,
            unit_price_snapshot=accessory.price,
            accessory=accessory,
        )

        response = self.client.post(
            reverse("catalogue:accessory_update", args=[accessory.pk]),
            self.accessory_data(name="Wireless mouse v2", price="30.00", active=""),
        )

        self.assertRedirects(response, reverse("catalogue:accessory_manage_list"))
        accessory.refresh_from_db()
        line.refresh_from_db()
        self.assertFalse(accessory.active)
        self.assertEqual(line.accessory, accessory)
        self.assertEqual(line.name, "Wireless mouse")
        self.assertEqual(line.unit_price_snapshot, Decimal("25.00"))
        self.assertEqual(
            list(StaffAction.objects.values_list("action_type", flat=True)),
            ["accessory_created", "accessory_deactivated"],
        )

    def test_accessory_price_must_be_positive(self):
        response = self.client.post(
            reverse("catalogue:accessory_create"), self.accessory_data(price="0")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Accessory.objects.count(), 0)
        self.assertContains(response, "Ensure this value is greater than or equal to 0.01")

    def test_customer_cannot_manage_accessories(self):
        self.client.logout()
        user = User.objects.create_user(username="customer", password="safe-pass-123")
        Customer.objects.create(user=user, name="Customer", email="customer@example.com")
        self.client.login(username="customer", password="safe-pass-123")

        response = self.client.get(reverse("catalogue:accessory_manage_list"))

        self.assertEqual(response.status_code, 302)
