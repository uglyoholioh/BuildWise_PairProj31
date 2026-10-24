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


class CustomerAccountTests(TestCase):
    def registration_data(self, **overrides):
        return {
            "username": "newbuyer",
            "name": "New Buyer",
            "email": "newbuyer@example.com",
            "delivery_address": "2 Example Road",
            "password1": "Buildwise!Pass482",
            "password2": "Buildwise!Pass482",
            **overrides,
        }

    def test_registration_preserves_session_and_returns_to_safe_intended_action(self):
        session = self.client.session
        session["cart"] = {"preset": 7, "quantity": 1}
        session.save()

        response = self.client.post(
            reverse("accounts:register") + "?next=%2Fsupport%2Fnew",
            self.registration_data(next="/support/new"),
        )

        self.assertRedirects(response, "/support/new", fetch_redirect_response=False)
        self.assertEqual(self.client.session["cart"]["preset"], 7)
        customer = Customer.objects.get(email="newbuyer@example.com")
        self.assertEqual(customer.user.email, customer.email)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_registration_rejects_case_insensitive_duplicate_email(self):
        user = User.objects.create_user(username="existing", password="Buildwise!Pass482")
        Customer.objects.create(user=user, name="Existing", email="existing@example.com")

        response = self.client.post(
            reverse("accounts:register"),
            self.registration_data(email="EXISTING@example.com"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists")
        self.assertEqual(Customer.objects.count(), 1)

    def test_login_preserves_session_and_redirects_to_safe_next(self):
        user = User.objects.create_user(username="buyer", password="Buildwise!Pass482")
        Customer.objects.create(user=user, name="Buyer", email="buyer@example.com")
        session = self.client.session
        session["cart"] = {"preset": 3}
        session.save()

        response = self.client.post(
            reverse("accounts:login") + "?next=%2Forders",
            {"username": "buyer", "password": "Buildwise!Pass482", "next": "/orders"},
        )

        self.assertRedirects(response, "/orders", fetch_redirect_response=False)
        self.assertEqual(self.client.session["cart"]["preset"], 3)

    def test_profile_and_password_can_be_updated_without_losing_session(self):
        user = User.objects.create_user(username="buyer", password="Buildwise!Pass482")
        customer = Customer.objects.create(user=user, name="Buyer", email="buyer@example.com")
        self.client.force_login(user)
        session = self.client.session
        session["cart"] = {"preset": 5}
        session.save()

        response = self.client.post(
            reverse("accounts:profile"),
            {"name": "Updated Buyer", "email": "updated@example.com", "delivery_address": "3 Example Road"},
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        customer.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(customer.name, "Updated Buyer")
        self.assertEqual(user.email, customer.email)

        response = self.client.post(
            reverse("accounts:profile"),
            {"change_password": "1", "old_password": "Buildwise!Pass482", "new_password1": "NewBuildwise!Pass482", "new_password2": "NewBuildwise!Pass482"},
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.assertEqual(self.client.session["cart"]["preset"], 5)
        self.assertTrue(self.client.get(reverse("accounts:profile")).wsgi_request.user.check_password("NewBuildwise!Pass482"))

    def test_customer_account_cannot_access_staff_queue(self):
        user = User.objects.create_user(username="buyer", password="Buildwise!Pass482")
        Customer.objects.create(user=user, name="Buyer", email="buyer@example.com")
        self.client.force_login(user)

        response = self.client.get(reverse("orders:order_queue"))

        self.assertEqual(response.status_code, 302)

    def test_employee_credentials_do_not_authenticate_on_customer_login(self):
        employee_user = User.objects.create_user(username="employee", password="Buildwise!Pass482")
        Employee.objects.create(user=employee_user)

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "employee", "password": "Buildwise!Pass482"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Username or password is incorrect")
