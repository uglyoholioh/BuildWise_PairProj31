from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Customer, Employee
from catalogue.models import Accessory, AssemblyFeeSetting, Component, Configuration, ConfigurationItem
from inventory.models import Reservation, StockAdjustment
from orders.allocations import component_snapshot
from orders.models import Order, OrderLine, PurchasedPC, SubstitutionProposal
from .accessories import accessory_suggestions


class PurchaseHistoryFixture:
    def setUp(self):
        super().setUp()
        self.employee = Employee.objects.create(user=User.objects.create_user("staff"))
        AssemblyFeeSetting.objects.create(fee_amount=25, updated_by=self.employee)
        self.preset = Configuration.objects.create(name="Gaming PC", status="published")
        specs = {
            Component.Category.CPU: {"socket": "AM5"},
            Component.Category.MOTHERBOARD: {"socket": "AM5", "memory_type": "DDR5", "form_factor": "ATX"},
            Component.Category.MEMORY: {"memory_type": "DDR5"},
            Component.Category.GPU: {"ml_tier": "high"},
            Component.Category.STORAGE: {}, Component.Category.POWER_SUPPLY: {},
            Component.Category.CASE: {"form_factor": "ATX"},
        }
        self.parts = []
        for category, fields in specs.items():
            part = Component.objects.create(category=category, brand="Example", model=category, price=100, **fields)
            ConfigurationItem.objects.create(configuration=self.preset, component=part)
            StockAdjustment.objects.create(item_type="component", component=part, delta=10, reason="Test stock", created_by=self.employee)
            self.parts.append(part)
        self.mouse = self.accessory("Suggested mouse", 20)

    def accessory(self, name, price):
        accessory = Accessory.objects.create(name=name, category="Peripheral", price=price)
        StockAdjustment.objects.create(item_type="accessory", accessory=accessory, delta=10,
                                       reason="Test stock", created_by=self.employee)
        return accessory

    def purchase(self, parts=None, customer=None, accessories=None, status="delivered", payment_status="paid", pc_status="quality_passed", refund=0):
        if customer is None:
            number = Customer.objects.count() + 1
            customer = Customer.objects.create(user=User.objects.create_user(f"buyer{number}"),
                                                name=f"Private buyer {number}", email=f"private{number}@example.test")
        order = Order.objects.create(customer=customer, status=status, payment_status=payment_status,
                                     total_snapshot=1000, delivery_name="Private recipient", delivery_address="Private address")
        line = OrderLine.objects.create(order=order, kind="build", name="Purchased PC", unit_price_snapshot=700,
            configuration=self.preset, custom_snapshot={"components": [component_snapshot(part) for part in (parts or self.parts)]})
        pc = PurchasedPC.objects.create(order_line=line, unit_index=1, status=pc_status, refund_amount=refund)
        for accessory in ([self.mouse] if accessories is None else accessories):
            OrderLine.objects.create(order=order, kind="accessory", name=accessory.name, accessory=accessory, unit_price_snapshot=accessory.price)
        return pc

    def suggestions(self):
        return accessory_suggestions(self.preset.items.select_related("component"))


class AccessorySuggestionTests(PurchaseHistoryFixture, TestCase):
    def test_cold_start_and_two_customers_omit_the_entire_section(self):
        url = reverse("catalogue:preset_detail", args=[self.preset.pk])
        self.assertEqual(self.suggestions(), [])
        self.assertNotContains(self.client.get(url), "Accessories bought with similar PCs")
        for unused in range(2):
            self.purchase()
        self.assertEqual(self.suggestions(), [])
        self.assertNotContains(self.client.get(url), "Suggested mouse")

    def test_three_customers_show_current_price_and_existing_cart_action(self):
        for unused in range(3):
            self.purchase()
        self.mouse.price = 35
        self.mouse.save()
        rows = self.suggestions()
        self.assertEqual([(row["accessory"].pk, row["purchase_count"]) for row in rows], [(self.mouse.pk, 3)])
        page = self.client.get(reverse("catalogue:preset_detail", args=[self.preset.pk]))
        for text in ("Suggested mouse", "$35.00", "3 completed orders", "Add accessory to cart"):
            self.assertContains(page, text)
        for text in ("Private buyer", "private1@example.test", "Private recipient", "Private address"):
            self.assertNotContains(page, text)
        self.assertEqual(Reservation.objects.count(), 0)
        self.client.post(reverse("orders:cart_add"), {"kind": "accessory", "item_id": self.mouse.pk})
        cart = self.client.session["cart"]
        self.assertEqual((cart[0]["accessory_id"], cart[0]["quantity"]), (self.mouse.pk, 1))

    def test_repeat_customer_orders_do_not_meet_minimum_evidence(self):
        first = self.purchase()
        customer = first.order_line.order.customer
        for unused in range(4):
            self.purchase(customer=customer)
        self.purchase()
        self.assertEqual(self.suggestions(), [])

    def test_bulk_pcs_and_duplicate_accessory_lines_count_one_order(self):
        first = self.purchase(accessories=[self.mouse, self.mouse])
        line = first.order_line
        line.quantity = 10
        line.save()
        for index in range(2, 11):
            PurchasedPC.objects.create(order_line=line, unit_index=index, status="quality_passed")
        self.purchase()
        self.assertEqual(self.suggestions(), [])
        self.purchase()
        self.assertEqual(self.suggestions()[0]["purchase_count"], 3)

    def test_pending_cancelled_refunded_or_undelivered_purchases_do_not_count(self):
        for options in ({"payment_status": "pending"}, {"payment_status": "refunded"}, {"status": "cancelled"},
                        {"status": "dispatched"}, {"pc_status": "cancelled"}, {"refund": 700}):
            with self.subTest(options=options):
                self.purchase(**options)
                self.assertEqual(self.suggestions(), [])
        self.purchase()
        self.purchase()
        self.assertEqual(self.suggestions(), [])

    def test_accessory_only_orders_and_pcs_without_accessories_do_not_count(self):
        for unused in range(3):
            pc = self.purchase(accessories=[])
            order = pc.order_line.order
            accessory_order = Order.objects.create(customer=order.customer, status="delivered", payment_status="paid",
                total_snapshot=20, delivery_name="Buyer", delivery_address="Road")
            OrderLine.objects.create(order=accessory_order, kind="accessory", name=self.mouse.name, accessory=self.mouse, unit_price_snapshot=20)
        self.assertEqual(self.suggestions(), [])

    def test_similarity_requires_cpu_gpu_and_five_matching_core_categories(self):
        different = list(self.parts)
        for index in (4, 5):
            original = self.parts[index]
            different[index] = Component.objects.create(category=original.category, brand="Other", model="Alternative", price=100)
        for unused in range(3):
            self.purchase(parts=different)
        self.assertEqual(len(self.suggestions()), 1)  # Five matching categories, including CPU and GPU.
        memory = Component.objects.create(category=Component.Category.MEMORY, brand="Other", model="Other RAM", price=100, memory_type="DDR5")
        self.preset.items.filter(component=self.parts[2]).update(component=memory)
        self.assertEqual(self.suggestions(), [])  # Only four categories match.
        self.preset.items.filter(component=memory).update(component=self.parts[2])
        for index in (0, 3):
            original = self.parts[index]
            alternative = Component.objects.create(category=original.category, brand="Other", model="Other core part", price=100,
                                                    socket=original.socket, ml_tier=original.ml_tier)
            self.preset.items.filter(component=original).update(component=alternative)
            self.assertEqual(self.suggestions(), [])
            self.preset.items.filter(component=alternative).update(component=original)

    def test_accepted_substitutions_determine_delivered_build_similarity(self):
        original_gpu = Component.objects.create(category=Component.Category.GPU, brand="Other", model="Before substitution", price=100)
        old_parts = list(self.parts)
        old_parts[3] = original_gpu
        pcs = [self.purchase(parts=old_parts) for unused in range(3)]
        self.assertEqual(self.suggestions(), [])
        for pc in pcs:
            SubstitutionProposal.objects.create(purchased_pc=pc, original_component=original_gpu,
                replacement_component=self.parts[3], proposed_by=self.employee, reason="Replacement", status="accepted",
                decided_at=timezone.now(), original_snapshot=component_snapshot(original_gpu),
                replacement_snapshot=component_snapshot(self.parts[3]))
        self.assertEqual(self.suggestions()[0]["purchase_count"], 3)

    def test_missing_history_does_not_fall_back_to_current_preset(self):
        for unused in range(3):
            pc = self.purchase()
            line = pc.order_line
            line.custom_snapshot = {}
            line.save()
        self.assertEqual(self.suggestions(), [])

    def test_unrecorded_substitution_details_and_cancelled_siblings_do_not_supply_evidence(self):
        replacement = Component.objects.create(category=Component.Category.GPU, brand="Other", model="Replacement", price=100)
        for unused in range(3):
            pc = self.purchase(pc_status="cancelled", refund=700)
            sibling = PurchasedPC.objects.create(order_line=pc.order_line, unit_index=2, status="quality_passed")
            SubstitutionProposal.objects.create(purchased_pc=sibling, original_component=self.parts[3],
                replacement_component=replacement, proposed_by=self.employee, reason="Old replacement",
                status="accepted", decided_at=timezone.now())
        self.assertEqual(self.suggestions(), [])

    def test_inactive_and_fully_reserved_accessories_are_omitted(self):
        for unused in range(3):
            self.purchase()
        self.mouse.active = False
        self.mouse.save()
        self.assertEqual(self.suggestions(), [])
        self.mouse.active = True
        self.mouse.save()
        Reservation.objects.create(accessory=self.mouse, quantity=10)
        self.assertEqual(self.suggestions(), [])

    def test_ranking_uses_distinct_orders_then_price_and_returns_at_most_three(self):
        cheap = self.accessory("Cheap keyboard", 10)
        frequent = self.accessory("Popular monitor", 200)
        expensive = self.accessory("Expensive headset", 100)
        for unused in range(3):
            self.purchase(accessories=[self.mouse, cheap, frequent, expensive])
        self.purchase(accessories=[frequent])
        self.assertEqual([row["accessory"] for row in self.suggestions()], [frequent, cheap, self.mouse])
        frequent.active = False
        frequent.save()
        self.assertEqual([row["accessory"] for row in self.suggestions()], [cheap, self.mouse, expensive])

    def test_custom_build_page_shows_suggestions_but_incomplete_build_does_not(self):
        for unused in range(3):
            self.purchase()
        page = self.client.get(reverse("catalogue:configure"), {"preset": self.preset.pk})
        self.assertContains(page, "Suggested mouse")
        session = self.client.session
        session["current_build"] = {"name": "Incomplete", "components": [{"component_id": self.parts[0].pk, "quantity": 1}]}
        session.save()
        self.assertNotContains(self.client.get(reverse("catalogue:configure")), "Accessories bought with similar PCs")

    def test_cart_rechecks_stock_after_suggestion_was_displayed(self):
        for unused in range(3):
            self.purchase()
        self.assertEqual(len(self.suggestions()), 1)
        StockAdjustment.objects.create(item_type="accessory", accessory=self.mouse, delta=-10,
                                       reason="Stock sold", created_by=self.employee)
        self.client.post(reverse("orders:cart_add"), {"kind": "accessory", "item_id": self.mouse.pk})
        self.assertFalse(self.client.session.get("cart"))
