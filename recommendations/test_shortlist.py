from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Customer, Employee
from catalogue.models import AssemblyFeeSetting, Component, Configuration, ConfigurationItem
from inventory.models import Reservation, StockAdjustment
from .models import ShortlistItem


class ShortlistTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(user=User.objects.create_user("buyer"), name="Buyer", email="buyer@example.test")
        self.employee = Employee.objects.create(user=User.objects.create_user("staff"))
        self.fee = AssemblyFeeSetting.objects.create(fee_amount=25, updated_by=self.employee)
        self.preset = Configuration.objects.create(name="Original preset", status="published")
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
            part = Component.objects.create(category=category, brand="Example", model=f"Original {category}", price=100, **fields)
            ConfigurationItem.objects.create(configuration=self.preset, component=part)
            StockAdjustment.objects.create(item_type="component", component=part, delta=4, reason="Test stock", created_by=self.employee)
            self.parts.append(part)
        self.client.force_login(self.customer.user)
        self.client.get(reverse("catalogue:configure"), {"preset": self.preset.pk})
        self.save_url = reverse("recommendations:shortlist_save")
        self.list_url = reverse("recommendations:shortlist")

    def save_build(self, name="My saved PC"):
        response = self.client.post(self.save_url, {"name": name})
        self.assertRedirects(response, self.list_url)
        return self.customer.shortlist.latest("pk")

    def reopen(self, saved):
        return self.client.post(reverse("recommendations:shortlist_reopen", args=[saved.pk]), follow=True)

    def compare(self, saved):
        return self.client.post(reverse("recommendations:compare_add"), {"kind": "shortlist", "item_id": saved.pk}, follow=True)

    def test_guest_draft_survives_sign_in_and_only_explicit_save_persists(self):
        self.client.logout()
        self.client.post(reverse("catalogue:configure"), {"name": "Unfinished guest PC", "component_cpu": self.parts[0].pk,
                                                         "action": "shortlist"})
        self.assertEqual(self.client.session["current_build"]["name"], "Unfinished guest PC")
        self.assertEqual(len(self.client.session["current_build"]["components"]), 1)
        self.assertEqual(self.client.get(self.save_url).status_code, 302)
        self.assertFalse(ShortlistItem.objects.exists())
        self.client.force_login(self.customer.user)
        self.assertContains(self.client.get(self.save_url), "Unfinished guest PC")
        self.assertFalse(ShortlistItem.objects.exists())
        saved = self.save_build("  Draft for later  ")
        self.assertEqual(saved.name, "Draft for later")
        self.assertEqual(len(saved.custom_snapshot["components"]), 1)
        self.assertFalse(Reservation.objects.exists())
        other_browser = Client()
        other_browser.force_login(self.customer.user)
        self.assertContains(other_browser.get(self.list_url), "Draft for later")

    def test_names_required_limited_and_ownership_is_not_taken_from_post(self):
        for name in ("", "   ", "x" * 161):
            self.assertEqual(self.client.post(self.save_url, {"name": name}).status_code, 200)
        self.assertFalse(ShortlistItem.objects.exists())
        self.client.post(self.save_url, {"name": "Own build", "customer": 999, "configuration": 999})
        saved = ShortlistItem.objects.get()
        self.assertEqual(saved.customer, self.customer)
        self.assertEqual(saved.configuration, self.preset)

    def test_multiple_saved_builds_and_reopening_do_not_overwrite_saved_copy(self):
        first = self.save_build("First option")
        second = self.save_build("Second option")
        original = first.custom_snapshot
        page = self.reopen(first)
        self.assertContains(page, "First option")
        self.client.post(reverse("catalogue:configure"), {"name": "Edited draft", "component_cpu": self.parts[0].pk})
        first.refresh_from_db()
        self.assertEqual(first.custom_snapshot, original)
        self.assertEqual(first.name, "First option")
        self.reopen(second)
        self.assertEqual(self.client.session["current_build"]["name"], "Second option")
        self.assertEqual(ShortlistItem.objects.count(), 2)

    def test_reopen_refreshes_prices_fee_stock_and_compatibility(self):
        saved = self.save_build()
        self.parts[0].price = 200
        self.parts[0].save()
        self.fee.fee_amount = 50
        self.fee.save()
        page = self.reopen(saved)
        self.assertEqual(page.context["price"], Decimal("850.00"))
        self.assertEqual(page.context["available"], 4)
        StockAdjustment.objects.create(item_type="component", component=self.parts[0], delta=-4,
                                       reason="Stock sold", created_by=self.employee)
        self.assertContains(self.reopen(saved), "Currently out of stock")
        self.parts[0].socket = "LGA"
        self.parts[0].save()
        page = self.reopen(saved)
        self.assertContains(page, "does not match")
        self.assertIsNone(page.context["price"])
        self.assertNotContains(page, "Add build to cart")

    def test_inactive_components_remain_visible_and_cannot_be_purchased(self):
        saved = self.save_build()
        self.parts[3].active = False
        self.parts[3].save()
        page = self.reopen(saved)
        self.assertContains(page, "Original gpu")
        self.assertContains(page, "No longer available")
        self.assertTrue(page.context["issues"])
        self.assertIsNone(page.context["price"])
        self.client.post(reverse("orders:cart_add"), {"kind": "custom"})
        self.assertFalse(self.client.session.get("cart"))

    def test_deleted_optional_component_is_flagged_in_reopen_and_comparison(self):
        extra = Component.objects.create(category=Component.Category.COOLER, brand="Example", model="Cooler", price=10)
        session = self.client.session
        build = session["current_build"]
        build["components"].append({"component_id": extra.pk, "quantity": 1})
        session["current_build"] = build
        session.save()
        saved = self.save_build()
        extra.delete()
        page = self.reopen(saved)
        self.assertContains(page, "is no longer in the catalogue")
        self.assertIsNone(page.context["price"])
        self.client.post(reverse("orders:cart_add"), {"kind": "custom"})
        self.assertFalse(self.client.session.get("cart"))
        page = self.compare(saved)
        self.assertContains(page, "Some saved components are no longer in the catalogue")
        self.assertIsNone(page.context["builds"][0]["price"])

    def test_saved_selection_survives_preset_changes_withdrawal_and_deletion(self):
        saved = self.save_build()
        self.preset.items.filter(component=self.parts[0]).delete()
        self.preset.status = "withdrawn"
        self.preset.save()
        page = self.compare(saved)
        self.assertContains(page, "Original cpu")
        self.assertEqual(page.context["builds"][0]["price"], Decimal("725.00"))
        self.preset.delete()
        saved.refresh_from_db()
        self.assertIsNone(saved.configuration)
        self.assertEqual(self.reopen(saved).context["price"], Decimal("725.00"))

    def test_quantities_and_preset_origin_survive_reopen_and_update(self):
        session = self.client.session
        build = session["current_build"]
        build["components"][0]["quantity"] = 2
        session["current_build"] = build
        session.save()
        saved = self.save_build()
        self.assertEqual(self.reopen(saved).context["price"], Decimal("825.00"))
        self.client.post(reverse("catalogue:configure"), {"name": "Updated", **{f"component_{part.category}": part.pk for part in self.parts}})
        self.assertEqual(self.client.session["current_build"]["components"][0]["quantity"], 2)
        self.client.post(reverse("orders:cart_add"), {"kind": "custom"})
        snapshot = self.client.session["cart"][0]["custom_snapshot"]
        self.assertEqual(snapshot["source_preset_id"], self.preset.pk)
        self.assertTrue(snapshot["customised_from_preset"])

    def test_customer_isolation_including_forged_reopen_remove_and_compare(self):
        saved = self.save_build("Private saved option")
        other = Customer.objects.create(user=User.objects.create_user("other"), name="Other", email="other@example.test")
        self.client.force_login(other.user)
        self.assertNotContains(self.client.get(self.list_url), "Private saved option")
        for action in ("shortlist_reopen", "shortlist_remove"):
            self.assertEqual(self.client.post(reverse("recommendations:" + action, args=[saved.pk])).status_code, 404)
        self.assertNotContains(self.compare(saved), "Private saved option")
        self.client.force_login(self.employee.user)
        self.assertEqual(self.client.get(self.list_url).status_code, 404)
        self.assertEqual(self.client.post(self.save_url, {"name": "Staff save"}).status_code, 404)
        self.assertEqual(ShortlistItem.objects.count(), 1)

    def test_three_build_limit_duplicate_addition_and_stale_selection_cleanup(self):
        builds = [self.save_build(f"Option {index}") for index in range(4)]
        for saved in builds[:3]:
            self.compare(saved)
        self.compare(builds[0])
        page = self.compare(builds[3])
        self.assertContains(page, "Compare up to three builds at a time")
        self.assertEqual(len(page.context["builds"]), 3)
        builds[0].delete()  # Removal in another session must not consume a slot.
        page = self.compare(builds[3])
        self.assertEqual(len(page.context["builds"]), 3)
        self.assertContains(page, "Option 3")
        other = Customer.objects.create(user=User.objects.create_user("other"), name="Other", email="other@example.test")
        self.client.force_login(other.user)
        page = self.client.get(reverse("recommendations:compare"))
        self.assertEqual(page.context["builds"], [])
        self.assertEqual(self.client.session["comparison_items"], [])

    def test_remove_preserves_current_draft_and_other_saved_builds(self):
        saved = self.save_build()
        other = self.save_build("Keep me")
        self.reopen(saved)
        draft = self.client.session["current_build"]
        self.compare(saved)
        response = self.client.post(reverse("recommendations:shortlist_remove", args=[saved.pk]))
        self.assertRedirects(response, self.list_url)
        self.assertEqual(list(self.customer.shortlist.values_list("pk", flat=True)), [other.pk])
        self.assertEqual(self.client.session["current_build"], draft)
        self.assertEqual(self.client.session["comparison_items"], [])

    def test_state_changing_actions_require_post_and_csrf(self):
        saved = self.save_build()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.customer.user)
        for action in ("shortlist_reopen", "shortlist_remove"):
            url = reverse("recommendations:" + action, args=[saved.pk])
            self.assertEqual(client.get(url).status_code, 405)
            self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(client.post(self.save_url, {"name": "Missing token"}).status_code, 403)
        self.assertTrue(ShortlistItem.objects.filter(pk=saved.pk).exists())

    def test_empty_draft_is_not_saved_and_missing_fee_is_not_a_zero_quote(self):
        self.fee.delete()
        saved = self.save_build()
        self.assertIsNone(self.reopen(saved).context["price"])
        session = self.client.session
        session["current_build"] = {}
        session.save()
        self.assertRedirects(self.client.post(self.save_url, {"name": "Empty"}), reverse("catalogue:configure"))
        self.assertEqual(ShortlistItem.objects.count(), 1)

    def test_invalid_comparison_identifiers_are_rejected_without_server_errors(self):
        for value in ("not-an-id", "0", "-1", "9" * 100):
            response = self.client.post(reverse("recommendations:compare_add"), {"kind": "shortlist", "item_id": value}, follow=True)
            self.assertContains(response, "Choose a valid build to compare")
            self.assertEqual(response.context["builds"], [])
