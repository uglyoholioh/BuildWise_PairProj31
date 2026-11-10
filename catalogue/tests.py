from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from accounts.models import Customer, Employee, StaffAction
from inventory.models import Reservation, StockAdjustment
from orders.models import Order, OrderLine
from recommendations.models import GameSettingSupport
from .compatibility import check_compatibility
from .forms import ComponentForm
from .models import Accessory, AssemblyFeeSetting, Component, ComponentConflict, Configuration
from .pricing import available_preset_quantity, calculate_pc_price


class DemoCatalogueSeedTests(TestCase):
    def test_seed_is_repeatable_and_builds_compatible_draft_presets(self):
        call_command("seed_demo_catalogue", stdout=StringIO())
        call_command("seed_demo_catalogue", stdout=StringIO())

        self.assertEqual(Component.objects.count(), 9)
        self.assertEqual(Accessory.objects.count(), 2)
        self.assertEqual(Configuration.objects.count(), 2)
        for preset in Configuration.objects.all():
            self.assertEqual(preset.status, Configuration.Status.DRAFT)
            self.assertEqual(preset.items.count(), 7)
            self.assertFalse(check_compatibility(preset.components.all()))
        self.assertEqual(Component.objects.filter(category=Component.Category.GPU).count(), 2)
        self.assertEqual(GameSettingSupport.objects.count(), 2)
        preset = Configuration.objects.first()
        preset.status = Configuration.Status.PUBLISHED
        preset.save(update_fields=("status",))
        item = preset.items.first()
        item.quantity = 3
        item.save(update_fields=("quantity",))
        call_command("seed_demo_catalogue", stdout=StringIO())
        preset.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(preset.status, Configuration.Status.PUBLISHED)
        self.assertEqual(item.quantity, 3)


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


class CustomerConfiguratorTests(TestCase):
    def test_saving_legacy_preset_keeps_both_storage_models(self):
        first, second = [
            Component.objects.create(category=Component.Category.STORAGE, brand="Example", model=name, price=100)
            for name in ("First drive", "Second drive")
        ]
        preset = Configuration.objects.create(name="Existing preset", status=Configuration.Status.PUBLISHED)
        preset.items.create(component=first, quantity=1)
        preset.items.create(component=second, quantity=1)
        url = reverse("catalogue:configure")
        self.client.get(url, {"preset": preset.pk})
        self.client.post(url, {"component_storage": first.pk, "name": "Same build"})
        self.assertEqual(self.client.session["current_build"]["components"], [
            {"component_id": first.pk, "quantity": 1}, {"component_id": second.pk, "quantity": 1},
        ])

    def test_guest_build_selections_persist_in_session(self):
        cpu = Component.objects.create(
            category=Component.Category.CPU, brand="Example", model="CPU", socket="AM5", price="100.00"
        )

        response = self.client.post(reverse("catalogue:configure"), {"component_cpu": str(cpu.pk), "name": "First build"})

        self.assertRedirects(response, reverse("catalogue:configure"))
        self.assertEqual(self.client.session["current_build"]["name"], "First build")
        page = self.client.get(reverse("catalogue:configure"))
        self.assertContains(page, "Example CPU")
        self.assertContains(page, "Configuration is missing required categories")

    def test_conflicting_component_option_shows_the_reason(self):
        cpu = Component.objects.create(
            category=Component.Category.CPU, brand="Example", model="CPU", socket="AM5", price="100.00"
        )
        gpu = Component.objects.create(
            category=Component.Category.GPU, brand="Example", model="GPU", ml_tier="high", price="200.00"
        )
        ComponentConflict.objects.create(component_a=cpu, component_b=gpu, reason="Known clearance issue")
        session = self.client.session
        session["current_build"] = {
            "name": "Test build", "components": [{"component_id": cpu.pk, "quantity": 1}]
        }
        session.save()

        response = self.client.get(reverse("catalogue:configure"))

        self.assertContains(response, "(CPU Example CPU and Graphics card Example GPU are marked incompatible: Known clearance issue)")

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


class PresetBrowseTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="manager", password="safe-pass-123")
        self.employee = Employee.objects.create(user=user, role=Employee.Role.MANAGER)
        AssemblyFeeSetting.objects.create(fee_amount=Decimal("25.00"), updated_by=self.employee)

    def create_build(self, name, gpu_tier="high", gpu_stock=1, status=Configuration.Status.PUBLISHED):
        specs = {
            Component.Category.CPU: {"socket": "AM5"},
            Component.Category.MOTHERBOARD: {"socket": "AM5", "memory_type": "DDR5", "form_factor": "ATX"},
            Component.Category.MEMORY: {"memory_type": "DDR5"},
            Component.Category.GPU: {"ml_tier": gpu_tier},
            Component.Category.STORAGE: {},
            Component.Category.POWER_SUPPLY: {},
            Component.Category.CASE: {"form_factor": "ATX"},
        }
        components = [
            Component.objects.create(
                category=category,
                brand="Example",
                model=f"{name} {category}",
                price=Decimal("200.00") if category == Component.Category.GPU else Decimal("100.00"),
                **fields,
            )
            for category, fields in specs.items()
        ]
        configuration = Configuration.objects.create(name=name, status=status)
        configuration.components.add(*components)
        for component in components:
            quantity = gpu_stock if component.category == Component.Category.GPU else 2
            if quantity:
                StockAdjustment.objects.create(
                    item_type="component", component=component, delta=quantity,
                    reason="Opening stock", created_by=self.employee,
                )
        return configuration

    def test_customer_sees_only_published_presets_and_can_open_out_of_stock(self):
        self.create_build("Available Build")
        unavailable = self.create_build("Out of Stock Build", gpu_stock=0)
        self.create_build("Draft Build", status=Configuration.Status.DRAFT)
        self.create_build("Withdrawn Build", status=Configuration.Status.WITHDRAWN)

        response = self.client.get(reverse("catalogue:preset_list"))

        self.assertContains(response, "Available Build")
        self.assertContains(response, "Out of Stock Build")
        self.assertNotContains(response, "Draft Build")
        self.assertNotContains(response, "Withdrawn Build")
        detail = self.client.get(reverse("catalogue:preset_detail", args=[unavailable.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Out of stock")
        self.assertContains(detail, "Customize this build")
        self.assertEqual(
            self.client.get(reverse("catalogue:preset_detail", args=[self.create_build("Another Draft", status=Configuration.Status.DRAFT).pk])).status_code,
            404,
        )

    def test_filters_apply_price_component_tier_and_stock_availability(self):
        self.create_build("High Tier", gpu_tier="high", gpu_stock=1)
        self.create_build("Low Tier", gpu_tier="low", gpu_stock=0)
        url = reverse("catalogue:preset_list")

        response = self.client.get(url, {"tier": "high", "availability": "in_stock", "min_price": "800", "max_price": "850"})

        self.assertContains(response, "High Tier")
        self.assertNotContains(response, "Low Tier")
        response = self.client.get(url, {"availability": "out_of_stock"})
        self.assertContains(response, "Low Tier")
        self.assertNotContains(response, "High Tier")

    def test_demo_drafts_are_previews_only_when_no_published_presets_exist(self):
        self.create_build("[Demo] Sample PC", status=Configuration.Status.DRAFT)
        self.create_build("Private Staff Draft", status=Configuration.Status.DRAFT)

        response = self.client.get(reverse("catalogue:preset_list"))

        self.assertContains(response, "Sample · not for sale")
        self.assertContains(response, "Example component selection")
        self.assertContains(response, "Sample PC")
        self.assertNotContains(response, "Private Staff Draft")
        self.assertNotContains(response, "View &amp; customise")

        self.create_build("Published PC")
        response = self.client.get(reverse("catalogue:preset_list"))
        self.assertContains(response, "Published PC")
        self.assertNotContains(response, "Sample · not for sale")


class AccessoryBrowseTests(TestCase):
    def test_active_accessories_can_be_searched_and_filtered_with_stock(self):
        user = User.objects.create_user(username="manager", password="safe-pass-123")
        employee = Employee.objects.create(user=user, role=Employee.Role.MANAGER)
        mouse = Accessory.objects.create(name="Wireless mouse", category="Mouse", price="25.00")
        Accessory.objects.create(name="Mechanical keyboard", category="Keyboard", price="60.00")
        StockAdjustment.objects.create(
            item_type="accessory", accessory=mouse, delta=3,
            reason="Opening stock", created_by=employee,
        )

        response = self.client.get(reverse("catalogue:accessory_list"), {"q": "wireless", "category": "Mouse"})

        self.assertContains(response, "Wireless mouse")
        self.assertContains(response, "3 available")
        self.assertNotContains(response, "Mechanical keyboard")
