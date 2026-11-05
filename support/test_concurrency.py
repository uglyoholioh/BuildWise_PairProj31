"""Run with --settings=buildwise.sqlite_test_settings to exercise SQLite writers."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipIf

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import close_old_connections, connections
from django.test import RequestFactory, TransactionTestCase

from accounts.models import Customer
from orders.models import Order, OrderLine, PurchasedPC
from .models import Review
from .views import review_create


@skipIf(not settings.DATABASES["default"].get("TEST", {}).get("NAME"), "Use file-backed sqlite_test_settings for concurrency tests")
class ReviewConcurrencyTests(TransactionTestCase):
    def test_simultaneous_submissions_create_one_review_without_overwriting(self):
        customer = Customer.objects.create(user=User.objects.create_user("buyer"), name="Buyer", email="buyer@example.test")
        order = Order.objects.create(customer=customer, status="delivered", payment_status="paid", total_snapshot=700,
                                     delivery_name="Buyer", delivery_address="Test road")
        line = OrderLine.objects.create(order=order, kind="build", name="PC", unit_price_snapshot=700)
        pc = PurchasedPC.objects.create(order_line=line, unit_index=1, status="quality_passed")
        barrier = Barrier(2)

        def submit(rating):
            close_old_connections()
            try:
                request = RequestFactory().post("/reviews/", {"rating": rating, "text": f"Feedback {rating}"})
                request.user = User.objects.get(pk=customer.user_id)
                request.session = {}
                request._messages = FallbackStorage(request)
                barrier.wait(timeout=10)
                return review_create(request, pc.pk).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, (4, 5)))
        self.assertEqual(responses, [302, 302])
        review = Review.objects.get()
        self.assertIn(review.rating, (4, 5))
        self.assertEqual(review.text, f"Feedback {review.rating}")
