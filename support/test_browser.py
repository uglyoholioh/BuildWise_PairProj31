"""Optional review workflow test: requires Playwright and local Chromium."""
import shutil
import unittest

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from accounts.models import Customer
from catalogue.models import Configuration
from orders.models import Order, OrderLine, PurchasedPC
from .models import Review

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright and shutil.which("chromium"), "Requires Playwright and Chromium")
class ReviewBrowserTests(StaticLiveServerTestCase):
    def test_delivered_pc_review_is_public_on_review_and_preset_pages(self):
        customer = Customer.objects.create(user=User.objects.create_user("buyer"), name="Buyer", email="buyer@example.test")
        preset = Configuration.objects.create(name="Gaming preset", status="published")
        order = Order.objects.create(customer=customer, status="delivered", payment_status="paid", total_snapshot=700,
                                     delivery_name="Buyer", delivery_address="Private delivery address")
        line = OrderLine.objects.create(order=order, kind="build", name="My gaming PC", unit_price_snapshot=700,
            custom_snapshot={"source_preset_id": preset.pk, "source_preset_name": preset.name, "customised_from_preset": True,
                             "components": [{"category": "gpu", "brand": "Example", "model": "Purchased GPU", "quantity": 1}]})
        pc = PurchasedPC.objects.create(order_line=line, unit_index=1, status="quality_passed")
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
            page.get_by_role("link", name="Review this PC", exact=True).click()
            page.get_by_label("Rating:", exact=True).select_option("4")
            page.get_by_label("Written feedback:", exact=True).fill("Smooth gaming and quiet fans.")
            page.get_by_role("button", name="Publish review", exact=True).click()
            page.wait_for_load_state()
            self.assertIn("Your review has been published", page.locator("main").inner_text())
            self.assertIn("4 / 5", page.locator("article").inner_text())
            page.goto(self.live_server_url + reverse("orders:order_detail", args=[order.pk]))
            self.assertEqual(page.get_by_role("link", name="Review this PC", exact=True).count(), 0)
            self.assertTrue(page.get_by_role("link", name="Your review: 4 / 5", exact=True).is_visible())
            context.clear_cookies()
            for url in (reverse("support:review_list"), reverse("catalogue:preset_detail", args=[preset.pk])):
                page.goto(self.live_server_url + url)
                card = page.locator("article")
                self.assertIn("Smooth gaming and quiet fans.", card.inner_text())
                self.assertIn("Purchased GPU", card.inner_text())
                self.assertIn("Customised from Gaming preset", card.inner_text())
                self.assertNotIn("Private delivery address", page.locator("main").inner_text())
            browser.close()
        review = Review.objects.get()
        self.assertEqual((review.purchased_pc_id, review.customer_id, review.rating), (pc.pk, customer.pk, 4))
