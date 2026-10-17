from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer, Employee, StaffAction
from catalogue.models import Accessory, Component, Configuration, ConfigurationItem
from inventory.models import Reservation
from .models import Order, OrderLine, OrderStatusHistory, PurchasedPC


class OrderQueueTests(TestCase):
    def setUp(self):
        staff_user = User.objects.create_user(username="staff", password="safe-pass-123")
        self.employee = Employee.objects.create(user=staff_user)
        self.client.login(username="staff", password="safe-pass-123")
        customer_user = User.objects.create_user(username="customer", password="safe-pass-123")
        self.customer = Customer.objects.create(
            user=customer_user, name="BuildWise Customer", email="customer@example.com"
        )
        self.component = Component.objects.create(
            category=Component.Category.CPU,
            brand="Example",
            model="CPU",
            socket="AM5",
            price=Decimal("100.00"),
        )
        self.configuration = Configuration.objects.create(
            name="Build", status=Configuration.Status.PUBLISHED
        )
        ConfigurationItem.objects.create(
            configuration=self.configuration, component=self.component, quantity=1
        )

    def create_paid_build_pc(self, reserve=True):
        order = Order.objects.create(
            customer=self.customer,
            payment_status="paid",
            total_snapshot=Decimal("100.00"),
            delivery_name="BuildWise Customer",
            delivery_address="1 Example Road",
        )
        line = OrderLine.objects.create(
            order=order,
            kind="build",
            name="Build",
            quantity=1,
            unit_price_snapshot=Decimal("100.00"),
            configuration=self.configuration,
        )
        pc = PurchasedPC.objects.create(order_line=line, unit_index=1)
        if reserve:
            Reservation.objects.create(
                component=self.component, purchased_pc=pc, quantity=1
            )
        return order, pc

    def test_queue_shows_paid_order_delivery_price_and_allocations(self):
        order, pc = self.create_paid_build_pc()

        response = self.client.get(reverse("orders:order_queue"))

        self.assertContains(response, f"Order #{order.pk}")
        self.assertContains(response, "1 Example Road")
        self.assertContains(response, "$100.00")
        self.assertContains(response, "Example CPU")
        self.assertContains(response, f"PC #{pc.pk}")

    def test_starting_pc_is_atomic_logged_and_moves_order_to_progress(self):
        order, pc = self.create_paid_build_pc()

        response = self.client.post(reverse("orders:start_order", args=[pc.pk]))

        self.assertRedirects(response, reverse("orders:order_queue"))
        pc.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(pc.status, "assembling")
        self.assertEqual(order.status, "in_progress")
        self.assertEqual(OrderStatusHistory.objects.get().employee, self.employee)
        self.assertEqual(StaffAction.objects.get().action_type, "pc_assembly_started")

    def test_missing_allocation_automatically_places_pc_on_hold(self):
        _, pc = self.create_paid_build_pc(reserve=False)

        self.client.post(reverse("orders:start_order", args=[pc.pk]))

        pc.refresh_from_db()
        self.assertEqual(pc.status, "on_hold")
        self.assertIn("not fully reserved", pc.hold_reason)
        self.assertEqual(StaffAction.objects.get().action_type, "pc_allocation_hold")

    def test_accessory_only_order_starts_packing_when_reserved(self):
        accessory = Accessory.objects.create(
            name="Mouse", category="peripheral", price=Decimal("25.00")
        )
        order = Order.objects.create(
            customer=self.customer,
            payment_status="paid",
            total_snapshot=Decimal("25.00"),
            delivery_name="BuildWise Customer",
            delivery_address="1 Example Road",
        )
        line = OrderLine.objects.create(
            order=order,
            kind="accessory",
            name="Mouse",
            quantity=2,
            unit_price_snapshot=Decimal("25.00"),
            accessory=accessory,
        )
        Reservation.objects.create(order_line=line, accessory=accessory, quantity=2)

        response = self.client.post(reverse("orders:start_packing", args=[order.pk]))

        self.assertRedirects(response, reverse("orders:order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, "packing")
        self.assertEqual(OrderStatusHistory.objects.get().employee, self.employee)

    def test_unpaid_order_cannot_start(self):
        order, pc = self.create_paid_build_pc()
        order.payment_status = "pending"
        order.save(update_fields=("payment_status",))

        self.client.post(reverse("orders:start_order", args=[pc.pk]))

        pc.refresh_from_db()
        self.assertEqual(pc.status, "queued")

    def test_customer_can_cancel_only_before_assembly_starts(self):
        order, pc = self.create_paid_build_pc()
        self.client.force_login(self.customer.user)

        self.client.post(reverse("orders:order_cancel", args=[order.pk]))

        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(OrderStatusHistory.objects.get().to_status, "cancelled")
        self.client.force_login(self.employee.user)
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        pc.refresh_from_db()
        self.assertEqual(pc.status, "queued")

    def test_customer_cannot_cancel_after_assembly_starts(self):
        order, pc = self.create_paid_build_pc()
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        self.client.force_login(self.customer.user)

        self.client.post(reverse("orders:order_cancel", args=[order.pk]))

        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")
