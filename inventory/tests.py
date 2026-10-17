from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Employee, StaffAction
from catalogue.models import Accessory, Component
from .availability import stock_position
from .models import Reservation, StockAdjustment


class InventoryTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="staff", password="safe-pass-123")
        self.employee = Employee.objects.create(user=user)
        self.client.login(username="staff", password="safe-pass-123")
        self.component = Component.objects.create(
            category=Component.Category.CPU,
            brand="Example",
            model="CPU",
            socket="AM5",
            price="100.00",
        )
        self.accessory = Accessory.objects.create(
            name="Mouse", category="peripheral", price="25.00"
        )

    def adjustment_data(self, **overrides):
        return {
            "item_type": "component",
            "component": str(self.component.pk),
            "accessory": "",
            "delta": "4",
            "reason": "Opening stock",
            **overrides,
        }

    def test_available_stock_subtracts_active_reservations_for_both_item_types(self):
        StockAdjustment.objects.create(
            item_type="component", component=self.component, delta=8,
            reason="Receipt", created_by=self.employee,
        )
        StockAdjustment.objects.create(
            item_type="accessory", accessory=self.accessory, delta=4,
            reason="Receipt", created_by=self.employee,
        )
        Reservation.objects.create(component=self.component, quantity=3)
        Reservation.objects.create(accessory=self.accessory, quantity=1)
        Reservation.objects.create(
            component=self.component, quantity=2, status=Reservation.Status.RELEASED
        )

        self.assertEqual(stock_position("component", self.component), {
            "physical": 8, "reserved": 3, "available": 5,
        })
        self.assertEqual(stock_position("accessory", self.accessory), {
            "physical": 4, "reserved": 1, "available": 3,
        })

    def test_adjustment_cannot_reduce_reserved_available_stock(self):
        StockAdjustment.objects.create(
            item_type="component", component=self.component, delta=8,
            reason="Receipt", created_by=self.employee,
        )
        Reservation.objects.create(component=self.component, quantity=3)

        response = self.client.post(
            reverse("inventory:adjustment_create"),
            self.adjustment_data(delta="-6", reason="Damaged stock"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(StockAdjustment.objects.count(), 1)
        self.assertContains(response, "Only 5 units are available after reservations")

    def test_adjustment_records_staff_actor_and_updates_available_quantity(self):
        response = self.client.post(
            reverse("inventory:adjustment_create"), self.adjustment_data()
        )

        self.assertRedirects(response, reverse("inventory:inventory_dashboard"))
        adjustment = StockAdjustment.objects.get()
        self.assertEqual(adjustment.created_by, self.employee)
        self.assertIsNotNone(adjustment.created_at)
        self.assertEqual(adjustment.delta, 4)
        self.assertEqual(StaffAction.objects.get().action_type, "stock_adjusted")
        self.assertEqual(stock_position("component", self.component)["available"], 4)

    def test_adjustment_requires_item_type_to_match_the_selected_item(self):
        form_data = self.adjustment_data(
            item_type="accessory", accessory=str(self.accessory.pk)
        )
        response = self.client.post(reverse("inventory:adjustment_create"), form_data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(StockAdjustment.objects.count(), 0)
