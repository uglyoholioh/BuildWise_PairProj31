"""Optional customer decision test: requires Playwright and local Chromium."""
import shutil
import unittest

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from accounts.models import Customer, Employee
from catalogue.models import Component
from inventory.models import Reservation, StockAdjustment
from recommendations.models import Game, GameSettingSupport, QuestionnaireGameSetting, QuestionnaireResponse
from .allocations import component_snapshot
from .models import Order, OrderLine, PurchasedPC

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright and shutil.which("chromium"), "Requires Playwright and Chromium")
class SubstitutionBrowserTests(StaticLiveServerTestCase):
    def test_customer_reviews_accepts_and_declines_separate_pcs(self):
        employee = Employee.objects.create(user=User.objects.create_user("staff"))
        customer = Customer.objects.create(user=User.objects.create_user("buyer"), name="Buyer", email="buyer@example.test")
        order = Order.objects.create(customer=customer, payment_status="paid", total_snapshot=1400,
                                     delivery_name="Buyer", delivery_address="Example road")
        categories = [
            (Component.Category.CPU, {"socket": "AM5"}),
            (Component.Category.MOTHERBOARD, {"socket": "AM5", "memory_type": "DDR5", "form_factor": "ATX"}),
            (Component.Category.MEMORY, {"memory_type": "DDR5"}),
            (Component.Category.GPU, {"ml_tier": "high"}),
            (Component.Category.STORAGE, {}),
            (Component.Category.POWER_SUPPLY, {}),
            (Component.Category.CASE, {"form_factor": "ATX"}),
        ]
        parts = [Component.objects.create(category=category, brand="Example", model=category, price=100, **fields)
                 for category, fields in categories]
        replacement = Component.objects.create(category=Component.Category.GPU, brand="Example", model="New GPU",
                                                price=200, ml_tier="enthusiast", specs={"VRAM (GB)": 16})
        for part in parts + [replacement]:
            StockAdjustment.objects.create(item_type="component", component=part, delta=5,
                                           reason="Test stock", created_by=employee)
        line = OrderLine.objects.create(order=order, kind="build", name="Gaming PC", quantity=2,
                                        unit_price_snapshot=700,
                                        custom_snapshot={"components": [component_snapshot(part) for part in parts]})
        game = Game.objects.create(name="Test Game", slug="test-game")
        GameSettingSupport.objects.create(game=game, resolution="1920x1080", graphics_preset="high")
        questionnaire = QuestionnaireResponse.objects.create(customer=customer, perf_level="balanced", max_spend=1500)
        QuestionnaireGameSetting.objects.create(response=questionnaire, game=game, fps_goal=90,
                                                resolution="1920x1080", graphics_preset="high")
        self.client.force_login(employee.user)
        pcs = []
        proposals = []
        for unit_index in (1, 2):
            pc = PurchasedPC.objects.create(order_line=line, unit_index=unit_index)
            for part in parts:
                Reservation.objects.create(component=part, purchased_pc=pc, quantity=1)
            response = self.client.post(reverse("orders:propose_substitution", args=[pc.pk]), {
                "original_component": parts[3].pk, "replacement_component": replacement.pk,
                "reason": "Replacement needed",
            })
            self.assertEqual(response.status_code, 302)
            pcs.append(pc)
            proposals.append(pc.substitutions.get())
        self.client.force_login(customer.user)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("chromium"), headless=True,
                                                 args=["--no-sandbox"])
            context = browser.new_context()
            context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                 "value": self.client.cookies[settings.SESSION_COOKIE_NAME].value,
                                 "url": self.live_server_url}])
            page = context.new_page()
            page.goto(self.live_server_url + reverse("orders:order_detail", args=[order.pk]))
            first = page.locator(f"#substitution-{proposals[0].pk}")
            self.assertIn("VRAM (GB)", first.inner_text())
            self.assertIn("76 FPS (estimate)", first.inner_text())
            self.assertIn("102 FPS (estimate)", first.inner_text())
            self.assertIn("$1400.00", first.inner_text())
            first.get_by_role("button", name="Accept replacement", exact=True).click()
            page.wait_for_load_state()
            self.assertIn("accepted", first.get_by_role("heading").first.inner_text())
            self.assertEqual(first.get_by_role("button").count(), 0)
            second = page.locator(f"#substitution-{proposals[1].pk}")
            second.get_by_role("button", name="Decline and refund this PC", exact=True).click()
            page.wait_for_load_state()
            self.assertIn("declined", second.get_by_role("heading").first.inner_text())
            self.assertIn("Your decision was recorded", second.inner_text())
            self.assertEqual(second.get_by_role("button").count(), 0)
            browser.close()
        for pc in pcs:
            pc.refresh_from_db()
        self.assertEqual(pcs[0].status, "queued")
        self.assertEqual((pcs[1].status, pcs[1].refund_amount), ("cancelled", 700))
        self.assertEqual(Reservation.objects.filter(purchased_pc=pcs[0], component=replacement, status="reserved").count(), 1)
        self.assertFalse(Reservation.objects.filter(purchased_pc=pcs[1], status="reserved").exists())
        order.refresh_from_db()
        self.assertEqual((order.status, order.total_snapshot), ("placed", 1400))
