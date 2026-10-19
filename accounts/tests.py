from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Customer, Employee
from orders.models import Order, OrderLine, OrderStatusHistory, Shipment
from support.models import SupportRequest


class CustomerRecordTests(TestCase):
    def setUp(self):
        staff = User.objects.create_user(username="staff", password="secret-password")
        Employee.objects.create(user=staff)
        self.client.login(username="staff", password="secret-password")
        user = User.objects.create_user(username="buyer", password="customer-secret")
        self.customer = Customer.objects.create(
            user=user,
            name="BuildWise Buyer",
            email="buyer@example.com",
            delivery_address="1 Example Road",
        )

    def test_customer_search_matches_name_email_and_numeric_id(self):
        for query in ("BuildWise", "buyer@example.com", str(self.customer.pk)):
            response = self.client.get(reverse("accounts:customer_list"), {"q": query})
            self.assertContains(response, "BuildWise Buyer")

        response = self.client.get(reverse("accounts:customer_list"), {"q": "no-match"})
        self.assertContains(response, "No matching customers")

    def test_detail_shows_snapshot_price_fulfilment_and_support_not_credentials(self):
        order = Order.objects.create(
            customer=self.customer,
            status="delivered",
            payment_status="paid",
            total_snapshot=Decimal("125.00"),
            delivery_name="BuildWise Buyer",
            delivery_address="1 Example Road",
        )
        OrderLine.objects.create(
            order=order,
            kind="accessory",
            name="Mouse",
            quantity=1,
            unit_price_snapshot=Decimal("25.00"),
            custom_snapshot={"components": [{"component_id": 1, "name": "Example CPU", "quantity": 1}]},
        )
        OrderStatusHistory.objects.create(order=order, to_status="delivered", note="Received")
        Shipment.objects.create(
            order=order, status="delivered", courier="Courier Co", tracking_ref="TRACK-1"
        )
        support_request = SupportRequest.objects.create(customer=self.customer, order=order)

        response = self.client.get(reverse("accounts:customer_detail", args=[self.customer.pk]))

        self.assertContains(response, "$25.00")
        self.assertContains(response, "TRACK-1")
        self.assertContains(response, "Received")
        self.assertContains(response, reverse("support:request_detail", args=[support_request.pk]))
        self.assertNotContains(response, "customer-secret")
        self.assertNotContains(response, "pbkdf2_sha256")

    def test_customer_account_cannot_open_staff_customer_records(self):
        customer_user = self.customer.user
        self.client.force_login(customer_user)

        response = self.client.get(reverse("accounts:customer_list"))

        self.assertEqual(response.status_code, 302)
