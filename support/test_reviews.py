from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Customer, Employee
from catalogue.models import Component, Configuration
from orders.allocations import component_snapshot
from orders.models import Order, OrderLine, PurchasedPC, SubstitutionProposal
from .models import Review


class ReviewTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(user=User.objects.create_user("buyer"),
                                                name="Private buyer name", email="private@example.test")
        self.preset = Configuration.objects.create(name="Gaming preset", status="published")
        self.part = Component.objects.create(category="gpu", brand="Example", model="Purchased GPU", price=100,
                                             specs={"VRAM": "8 GB"})
        self.order = Order.objects.create(customer=self.customer, status="delivered", payment_status="paid",
                                         total_snapshot=1400, delivery_name="Private recipient", delivery_address="Private address")
        self.line = OrderLine.objects.create(order=self.order, kind="build", name="Purchased build", quantity=2,
                                            configuration=self.preset, unit_price_snapshot=700,
                                            custom_snapshot={"components": [component_snapshot(self.part)]})
        self.pc = PurchasedPC.objects.create(order_line=self.line, unit_index=1, status="quality_passed")
        self.sibling = PurchasedPC.objects.create(order_line=self.line, unit_index=2, status="quality_passed")
        self.url = reverse("support:review_create", args=[self.pc.pk])
        self.client.force_login(self.customer.user)

    def submit(self, **overrides):
        return self.client.post(self.url, {"rating": "5", "text": "Runs my games well.", **overrides})

    def test_owner_can_publish_one_review_for_each_delivered_pc(self):
        self.assertContains(self.client.get(reverse("orders:order_detail", args=[self.order.pk])), "Review this PC", count=2)
        self.assertEqual(Review.objects.count(), 0)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.submit(customer=999, purchased_pc=self.sibling.pk)
        review = Review.objects.get()
        self.assertRedirects(response, reverse("support:review_detail", args=[review.pk]))
        self.assertEqual((review.customer, review.purchased_pc, review.rating), (self.customer, self.pc, 5))
        self.submit(rating=1, text="Duplicate submission")
        review.refresh_from_db()
        self.assertEqual((Review.objects.count(), review.rating, review.text), (1, 5, "Runs my games well."))
        self.client.post(reverse("support:review_create", args=[self.sibling.pk]), {"rating": 1, "text": "Second physical PC"})
        self.assertEqual(Review.objects.count(), 2)
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(page, "Your review:", count=2)
        self.assertNotContains(page, "Review this PC")

    def test_invalid_ratings_and_blank_feedback_are_rejected(self):
        for rating in ("0", "6", "-1", "1.5", "abc", ""):
            with self.subTest(rating=rating):
                self.assertEqual(self.submit(rating=rating).status_code, 200)
        for text in ("", "   \n "):
            self.assertEqual(self.submit(text=text).status_code, 200)
        self.assertFalse(Review.objects.exists())

    def test_guests_other_customers_and_staff_cannot_submit(self):
        self.client.logout()
        self.assertEqual(self.submit().status_code, 302)
        for username in ("other", "staff"):
            user = User.objects.create_user(username)
            if username == "other":
                Customer.objects.create(user=user, name="Other", email="other@example.test")
            else:
                Employee.objects.create(user=user)
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.submit().status_code, 404)
        self.assertFalse(Review.objects.exists())

    def test_undelivered_cancelled_and_refunded_pcs_cannot_be_reviewed(self):
        for status in ("placed", "in_progress", "packing", "dispatched", "cancelled"):
            self.order.status = status
            self.order.save()
            self.submit()
        self.order.status = "delivered"
        self.order.save()
        for status in ("queued", "on_hold", "cancelled"):
            self.pc.status = status
            self.pc.save()
            self.submit()
        self.pc.status = "quality_passed"
        self.pc.refund_amount = 700
        self.pc.save()
        self.submit()
        self.pc.refund_amount = 0
        self.pc.save()
        self.order.payment_status = "refunded"
        self.order.save()
        self.submit()
        self.assertFalse(Review.objects.exists())

    def test_public_reviews_preserve_specs_escape_feedback_and_hide_private_data(self):
        self.submit(text="<script>alert('x')</script>\nGood build")
        self.part.model = "Later catalogue GPU"
        self.part.specs = {"VRAM": "24 GB"}
        self.part.save()
        self.client.logout()
        page = self.client.get(reverse("support:review_list"))
        for text in ("Purchased GPU", "8 GB", "5 / 5", "Verified purchaser", "&lt;script&gt;", "Good build"):
            self.assertContains(page, text)
        for text in ("Later catalogue GPU", "24 GB", "<script>", "private@example.test", "Private buyer name",
                     "Private recipient", "Private address"):
            self.assertNotContains(page, text)

    def test_approved_substitution_appears_in_review_and_remains_saved(self):
        staff = Employee.objects.create(user=User.objects.create_user("staff"))
        replacement = Component.objects.create(category="gpu", brand="Example", model="Approved GPU", price=100)
        proposal = SubstitutionProposal.objects.create(purchased_pc=self.pc, original_component=self.part,
            replacement_component=replacement, proposed_by=staff, reason="Replacement", status="accepted",
            original_snapshot=component_snapshot(self.part), replacement_snapshot=component_snapshot(replacement))
        self.submit()
        proposal.replacement_snapshot["model"] = "Later description"
        proposal.save()
        page = self.client.get(reverse("support:review_list"))
        self.assertContains(page, "Approved GPU")
        self.assertContains(page, "customer-approved component substitutions")
        self.assertNotContains(page, "Purchased GPU")
        self.assertNotContains(page, "Later description")

    def test_preset_page_includes_direct_and_customised_reviews_only(self):
        self.submit(text="Direct preset review")
        self.line.configuration = None
        self.line.custom_snapshot.update(source_preset_id=self.preset.pk, source_preset_name=self.preset.name,
                                         customised_from_preset=True)
        # Use a separate line so the direct purchase keeps its original provenance.
        self.line.pk = None
        self.line.save()
        custom_pc = PurchasedPC.objects.create(order_line=self.line, unit_index=1, status="quality_passed")
        self.client.post(reverse("support:review_create", args=[custom_pc.pk]), {"rating": 4, "text": "Custom preset review"})
        self.line.custom_snapshot.pop("source_preset_id")
        self.line.custom_snapshot.pop("source_preset_name")
        self.line.custom_snapshot.pop("customised_from_preset")
        self.line.pk = None
        self.line.save()
        unrelated = PurchasedPC.objects.create(order_line=self.line, unit_index=1, status="quality_passed")
        self.client.post(reverse("support:review_create", args=[unrelated.pk]), {"rating": 3, "text": "Unrelated review"})
        self.client.logout()
        page = self.client.get(reverse("catalogue:preset_detail", args=[self.preset.pk]))
        self.assertContains(page, "Direct preset review")
        self.assertContains(page, "Custom preset review")
        self.assertContains(page, "Customised from Gaming preset")
        self.assertNotContains(page, "Unrelated review")

    def test_legacy_order_does_not_present_live_catalogue_as_purchased_specs(self):
        self.line.custom_snapshot = {}
        self.line.save()
        self.submit()
        page = self.client.get(reverse("support:review_list"))
        self.assertContains(page, "Purchase-time specifications were not recorded")
        self.assertNotContains(page, "Purchased GPU")

    def test_csrf_and_get_do_not_allow_creation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.customer.user)
        self.assertEqual(client.get(self.url).status_code, 200)
        self.assertEqual(client.post(self.url, {"rating": 5, "text": "No token"}).status_code, 403)
        self.assertFalse(Review.objects.exists())

    def test_database_rejects_out_of_range_rating_and_duplicate_pc(self):
        for rating in (0, 6):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Review.objects.create(purchased_pc=self.pc, customer=self.customer, rating=rating, text="Invalid")
        self.submit()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Review.objects.create(purchased_pc=self.pc, customer=self.customer, rating=3, text="Duplicate")

    def test_empty_and_paginated_public_pages(self):
        self.client.logout()
        self.assertContains(self.client.get(reverse("support:review_list")), "No customer reviews yet")
        for index in range(13):
            pc = PurchasedPC.objects.create(order_line=self.line, unit_index=index + 3, status="quality_passed")
            Review.objects.create(purchased_pc=pc, customer=self.customer, rating=4, text=f"Review {index}")
        response = self.client.get(reverse("support:review_list"))
        self.assertEqual(len(response.context["page_obj"]), 12)
        self.assertContains(response, "Next")
        response = self.client.get(reverse("support:review_list"), {"page": 2})
        self.assertEqual(len(response.context["page_obj"]), 1)
        self.assertEqual(self.client.get(reverse("support:review_list"), {"page": "bad"}).status_code, 200)
