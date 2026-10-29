from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Customer, Employee, StaffAction
from catalogue.models import Accessory, AssemblyFeeSetting, Component, Configuration, ConfigurationItem
from inventory.models import Reservation, StockAdjustment
from .models import Order, OrderLine, OrderStatusHistory, PurchasedPC, QualityCheck, Shipment, SubstitutionProposal


class CheckoutTests(TestCase):
    def setUp(self):
        staff_user = User.objects.create_user(username="manager", password="safe-pass-123")
        self.employee = Employee.objects.create(user=staff_user, role=Employee.Role.MANAGER)
        AssemblyFeeSetting.objects.create(fee_amount=Decimal("25.00"), updated_by=self.employee)
        self.user = User.objects.create_user(username="buyer", password="safe-pass-123")
        Customer.objects.create(user=self.user, name="Buyer", email="buyer@example.com", delivery_address="1 Old Road")
        specs = {
            Component.Category.CPU: {"socket": "AM5"},
            Component.Category.MOTHERBOARD: {"socket": "AM5", "memory_type": "DDR5", "form_factor": "ATX"},
            Component.Category.MEMORY: {"memory_type": "DDR5"},
            Component.Category.GPU: {"ml_tier": "high"},
            Component.Category.STORAGE: {},
            Component.Category.POWER_SUPPLY: {},
            Component.Category.CASE: {"form_factor": "ATX"},
        }
        self.configuration = Configuration.objects.create(name="Checkout PC", status=Configuration.Status.PUBLISHED)
        for category, fields in specs.items():
            component = Component.objects.create(category=category, brand="Example", model=category, price="100.00", **fields)
            ConfigurationItem.objects.create(configuration=self.configuration, component=component, quantity=1)
            StockAdjustment.objects.create(item_type="component", component=component, delta=3, reason="Opening stock", created_by=self.employee)

    def test_preset_checkout_creates_paid_order_pc_snapshots_and_reservations(self):
        self.client.post(reverse("orders:cart_add"), {"kind": "preset", "item_id": self.configuration.pk})
        cart_line = self.client.session["cart"][0]
        self.client.post(reverse("orders:cart"), {"line_id": cart_line["id"], "action": "update", "quantity": "2"})
        self.client.get(reverse("orders:cart"))
        self.client.force_login(self.user)

        response = self.client.post(reverse("orders:checkout"), {"name": "Buyer", "delivery_address": "2 New Road", "confirm_prices": "on"})

        order = Order.objects.get()
        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.total_snapshot, Decimal("1450.00"))
        self.assertEqual(PurchasedPC.objects.filter(order_line__order=order).count(), 2)
        self.assertEqual(Reservation.objects.filter(purchased_pc__order_line__order=order, status=Reservation.Status.RESERVED).count(), 14)
        self.assertEqual(order.lines.get().custom_snapshot["components"][0]["price"], "100.00")
        self.assertEqual(self.client.session["cart"], [])

    def test_insufficient_combined_stock_keeps_cart_and_creates_no_order(self):
        for component in Component.objects.all():
            StockAdjustment.objects.create(item_type="component", component=component, delta=-2, reason="Reduce stock", created_by=self.employee)
        self.client.post(reverse("orders:cart_add"), {"kind": "preset", "item_id": self.configuration.pk})
        cart_line = self.client.session["cart"][0]
        self.client.post(reverse("orders:cart"), {"line_id": cart_line["id"], "action": "update", "quantity": "2"})
        self.client.get(reverse("orders:cart"))
        self.client.force_login(self.user)

        response = self.client.post(reverse("orders:checkout"), {"name": "Buyer", "delivery_address": "2 New Road", "confirm_prices": "on"})

        self.assertRedirects(response, reverse("orders:cart"))
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(len(self.client.session["cart"]), 1)

    def test_pc_and_accessory_share_checkout_and_reserve_accessory_stock(self):
        accessory = Accessory.objects.create(name="Wireless mouse", category="Mouse", price="40.00")
        StockAdjustment.objects.create(
            item_type="accessory", accessory=accessory, delta=2,
            reason="Opening stock", created_by=self.employee,
        )
        self.client.post(reverse("orders:cart_add"), {"kind": "preset", "item_id": self.configuration.pk})
        self.client.post(reverse("orders:cart_add"), {"kind": "accessory", "item_id": accessory.pk})
        self.client.get(reverse("orders:cart"))
        self.client.force_login(self.user)

        response = self.client.post(reverse("orders:checkout"), {
            "name": "Buyer", "delivery_address": "2 New Road", "confirm_prices": "on",
        })

        order = Order.objects.get()
        accessory_line = order.lines.get(kind="accessory")
        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        self.assertEqual(order.total_snapshot, Decimal("765.00"))
        self.assertEqual(accessory_line.accessory, accessory)
        self.assertEqual(accessory_line.reservations.get().quantity, 1)
        self.assertEqual(PurchasedPC.objects.filter(order_line__order=order).count(), 1)

    def test_accessory_only_checkout_creates_a_packable_order_line(self):
        accessory = Accessory.objects.create(name="Keyboard", category="Keyboard", price="60.00")
        StockAdjustment.objects.create(
            item_type="accessory", accessory=accessory, delta=2,
            reason="Opening stock", created_by=self.employee,
        )
        self.client.post(reverse("orders:cart_add"), {"kind": "accessory", "item_id": accessory.pk})
        self.client.get(reverse("orders:cart"))
        self.client.force_login(self.user)

        response = self.client.post(reverse("orders:checkout"), {
            "name": "Buyer", "delivery_address": "2 New Road", "confirm_prices": "on",
        })

        order = Order.objects.get()
        line = order.lines.get()
        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        self.assertEqual(order.total_snapshot, Decimal("60.00"))
        self.assertEqual(line.kind, "accessory")
        self.assertEqual(line.reservations.get().accessory, accessory)
        self.assertFalse(PurchasedPC.objects.filter(order_line__order=order).exists())


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

    def create_accessory_order(self, status="placed"):
        accessory = Accessory.objects.create(
            name="Mouse", category="peripheral", price=Decimal("25.00")
        )
        order = Order.objects.create(
            customer=self.customer,
            payment_status="paid",
            status=status,
            total_snapshot=Decimal("50.00"),
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
        return order, accessory

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
        order, _ = self.create_accessory_order()

        response = self.client.post(reverse("orders:start_packing", args=[order.pk]))

        self.assertRedirects(response, reverse("orders:order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, "packing")
        self.assertEqual(OrderStatusHistory.objects.get().employee, self.employee)

    def test_dispatch_consumes_accessory_reservation_once(self):
        order, accessory = self.create_accessory_order()
        StockAdjustment.objects.create(
            item_type="accessory", accessory=accessory, delta=3,
            reason="Opening stock", created_by=self.employee,
        )
        self.client.post(reverse("orders:start_packing", args=[order.pk]))

        self.client.post(
            reverse("orders:dispatch_order", args=[order.pk]),
            {"courier": "Courier Co", "tracking_ref": "TRACK-123"},
        )

        order.refresh_from_db()
        shipment = Shipment.objects.get(order=order)
        reservation = Reservation.objects.get(order_line__order=order)
        self.assertEqual(order.status, "dispatched")
        self.assertEqual(shipment.status, "dispatched")
        self.assertEqual(shipment.courier, "Courier Co")
        self.assertEqual(shipment.tracking_ref, "TRACK-123")
        self.assertIsNotNone(shipment.dispatched_at)
        self.assertEqual(reservation.status, Reservation.Status.CONSUMED)
        self.client.post(
            reverse("orders:dispatch_order", args=[order.pk]),
            {"courier": "Courier Co", "tracking_ref": "TRACK-123"},
        )
        self.assertEqual(Shipment.objects.filter(order=order).count(), 1)
        self.assertEqual(
            StockAdjustment.objects.filter(reason__contains="dispatched").count(), 1
        )

    def test_customer_delivery_confirmation_is_shared_and_idempotent(self):
        order, _ = self.create_accessory_order(status="dispatched")
        Shipment.objects.create(
            order=order, status="dispatched", dispatched_at=timezone.now()
        )
        self.client.force_login(self.customer.user)

        self.client.post(reverse("orders:confirm_receipt", args=[order.pk]))
        shipment = Shipment.objects.get(order=order)
        delivered_at = shipment.delivered_at
        self.client.post(reverse("orders:confirm_receipt", args=[order.pk]))

        order.refresh_from_db()
        shipment.refresh_from_db()
        self.assertEqual(order.status, "delivered")
        self.assertEqual(shipment.status, "delivered")
        self.assertEqual(shipment.confirmed_by, f"customer:{self.customer.user_id}")
        self.assertEqual(shipment.delivered_at, delivered_at)
        self.assertEqual(OrderStatusHistory.objects.filter(to_status="delivered").count(), 1)

    def create_substitution(self):
        order, pc = self.create_paid_build_pc()
        replacement = Component.objects.create(
            category=Component.Category.CPU,
            brand="Example",
            model="Replacement CPU",
            socket="AM5",
            price=Decimal("150.00"),
        )
        StockAdjustment.objects.create(
            item_type="component", component=replacement, delta=2,
            reason="Opening stock", created_by=self.employee,
        )
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        response = self.client.post(
            reverse("orders:propose_substitution", args=[pc.pk]),
            {
                "original_component": self.component.pk,
                "replacement_component": replacement.pk,
                "reason": "The original CPU is unavailable",
                "spec_differences": "Higher boost clock",
                "fps_note": "Estimated FPS is unchanged",
            },
        )
        self.assertEqual(response.status_code, 302)
        return order, pc, replacement, SubstitutionProposal.objects.get()

    def test_substitution_acceptance_rechecks_and_changes_only_allocations(self):
        order, pc, replacement, proposal = self.create_substitution()
        self.client.force_login(self.customer.user)

        response = self.client.post(
            reverse("orders:decide_substitution", args=[proposal.pk]),
            {"decision": "accept"},
        )

        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        pc.refresh_from_db()
        order.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(pc.status, "assembling")
        self.assertEqual(order.total_snapshot, Decimal("100.00"))
        self.assertEqual(proposal.status, "accepted")
        self.assertEqual(proposal.decided_by, f"customer:{self.customer.user_id}")
        self.assertEqual(
            list(Reservation.objects.filter(purchased_pc=pc, status=Reservation.Status.RESERVED).values_list("component_id", flat=True)),
            [replacement.pk],
        )
        self.assertEqual(
            Reservation.objects.get(purchased_pc=pc, component=self.component).status,
            Reservation.Status.RELEASED,
        )

    def test_unavailable_replacement_keeps_pc_on_hold_and_proposal_pending(self):
        order, pc, replacement, proposal = self.create_substitution()
        Reservation.objects.create(component=replacement, order_line=pc.order_line, quantity=2)
        self.client.force_login(self.customer.user)

        self.client.post(
            reverse("orders:decide_substitution", args=[proposal.pk]),
            {"decision": "accept"},
        )

        pc.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(pc.status, "on_hold")
        self.assertEqual(proposal.status, "pending")
        self.assertIn("no longer available", pc.hold_reason)

    def test_declining_substitution_records_full_refund_and_preserves_started_parts_for_inspection(self):
        order, pc, _, proposal = self.create_substitution()
        self.client.force_login(self.customer.user)

        self.client.post(
            reverse("orders:decide_substitution", args=[proposal.pk]),
            {"decision": "decline"},
        )

        pc.refresh_from_db()
        proposal.refresh_from_db()
        reservation = Reservation.objects.get(purchased_pc=pc, component=self.component)
        self.assertEqual(pc.status, "cancelled")
        self.assertEqual(pc.refund_amount, Decimal("100.00"))
        self.assertEqual(reservation.status, Reservation.Status.RESERVED)
        self.assertEqual(proposal.status, "declined")
        self.assertTrue(proposal.decided_at)

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
        self.assertEqual(order.payment_status, "refunded")
        self.assertEqual(OrderStatusHistory.objects.get().to_status, "cancelled")
        reservation = Reservation.objects.get(purchased_pc=pc)
        self.assertEqual(reservation.status, Reservation.Status.RELEASED)
        self.client.force_login(self.employee.user)
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        pc.refresh_from_db()
        self.assertEqual(pc.status, "cancelled")

    def test_customer_cannot_cancel_after_assembly_starts(self):
        order, pc = self.create_paid_build_pc()
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        self.client.force_login(self.customer.user)

        self.client.post(reverse("orders:order_cancel", args=[order.pk]))

        order.refresh_from_db()
        self.assertEqual(order.status, "in_progress")

    def test_customer_order_list_shows_only_their_orders_and_tracking(self):
        order, _ = self.create_accessory_order(status="dispatched")
        Shipment.objects.create(order=order, status="dispatched", courier="Courier Co", tracking_ref="TRACK-1")
        other_user = User.objects.create_user(username="other", password="safe-pass-123")
        Customer.objects.create(user=other_user, name="Other", email="other@example.com")
        other_order = Order.objects.create(customer=Customer.objects.get(user=other_user), total_snapshot=Decimal("1.00"), delivery_name="Other", delivery_address="Elsewhere")
        self.client.force_login(self.customer.user)

        response = self.client.get(reverse("orders:order_list"))

        self.assertContains(response, f"Order #{order.pk}")
        self.assertContains(response, "TRACK-1")
        self.assertNotContains(response, f"Order #{other_order.pk}")

    def test_passing_all_quality_checks_consumes_allocations_once(self):
        order, pc = self.create_paid_build_pc()
        StockAdjustment.objects.create(
            item_type="component", component=self.component, delta=2,
            reason="Opening stock", created_by=self.employee,
        )
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        self.client.post(reverse("orders:start_quality_check", args=[pc.pk]))

        for item in ("installed_parts", "startup", "stability"):
            response = self.client.post(
                reverse("orders:record_quality_check", args=[pc.pk]),
                {"checklist_item": item, "passed": "yes", "note": "Verified"},
            )
            self.assertEqual(response.status_code, 302)

        pc.refresh_from_db()
        reservation = Reservation.objects.get(purchased_pc=pc)
        self.assertEqual(pc.status, "quality_passed")
        self.assertEqual(reservation.status, Reservation.Status.CONSUMED)
        self.assertEqual(
            StockAdjustment.objects.filter(reason__contains="passed quality checks").count(), 1
        )
        self.client.post(
            reverse("orders:record_quality_check", args=[pc.pk]),
            {"checklist_item": "stability", "passed": "yes"},
        )
        self.assertEqual(
            StockAdjustment.objects.filter(reason__contains="passed quality checks").count(), 1
        )
        self.assertEqual(QualityCheck.objects.filter(purchased_pc=pc).count(), 3)

    def test_failed_faulty_part_is_removed_and_pc_held_for_substitution_if_unavailable(self):
        _, pc = self.create_paid_build_pc()
        StockAdjustment.objects.create(
            item_type="component", component=self.component, delta=1,
            reason="Opening stock", created_by=self.employee,
        )
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        self.client.post(reverse("orders:start_quality_check", args=[pc.pk]))

        self.client.post(
            reverse("orders:record_quality_check", args=[pc.pk]),
            {
                "checklist_item": "installed_parts", "passed": "no",
                "faulty_component": self.component.pk, "note": "Fan does not spin",
            },
        )

        pc.refresh_from_db()
        reservation = Reservation.objects.get(purchased_pc=pc)
        self.assertEqual(pc.status, "on_hold")
        self.assertIn("US-A11", pc.hold_reason)
        self.assertEqual(reservation.status, Reservation.Status.RELEASED)
        self.assertEqual(StockAdjustment.objects.order_by("-pk").first().delta, -1)

    def test_failed_faulty_part_with_spare_stock_enters_corrective_work_and_can_pass_retest(self):
        _, pc = self.create_paid_build_pc()
        StockAdjustment.objects.create(
            item_type="component", component=self.component, delta=2,
            reason="Opening stock", created_by=self.employee,
        )
        self.client.post(reverse("orders:start_order", args=[pc.pk]))
        self.client.post(reverse("orders:start_quality_check", args=[pc.pk]))
        self.client.post(
            reverse("orders:record_quality_check", args=[pc.pk]),
            {
                "checklist_item": "installed_parts", "passed": "no",
                "faulty_component": self.component.pk, "note": "First unit was faulty",
            },
        )
        pc.refresh_from_db()
        self.assertEqual(pc.status, "corrective_work")

        for item in ("installed_parts", "startup", "stability"):
            self.client.post(
                reverse("orders:record_quality_check", args=[pc.pk]),
                {"checklist_item": item, "passed": "yes", "note": "Retested"},
            )

        pc.refresh_from_db()
        self.assertEqual(pc.status, "quality_passed")
        self.assertEqual(
            StockAdjustment.objects.filter(reason__contains="passed quality checks").count(), 1
        )
