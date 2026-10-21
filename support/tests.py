from django.test import TestCase

# Create your tests here.
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer, Employee, StaffAction
from orders.models import Order
from .models import SupportMessage, SupportRequest


class SupportConversationTests(TestCase):
    def setUp(self):
        staff_user = User.objects.create_user(username="staff", password="safe-pass-123")
        self.employee = Employee.objects.create(user=staff_user)
        customer_user = User.objects.create_user(username="buyer", password="safe-pass-123")
        self.customer = Customer.objects.create(
            user=customer_user, name="Buyer", email="buyer@example.com"
        )
        self.order = Order.objects.create(
            customer=self.customer,
            total_snapshot=Decimal("100.00"),
            delivery_name="Buyer",
            delivery_address="1 Example Road",
        )

    def test_customer_creates_and_sees_only_own_public_thread(self):
        self.client.force_login(self.customer.user)
        response = self.client.post(
            reverse("support:request_create"),
            {"order": self.order.pk, "body": "Please check my order", "is_staff_only": "on"},
        )

        support_request = SupportRequest.objects.get()
        self.assertRedirects(response, reverse("support:request_detail", args=[support_request.pk]))
        message = SupportMessage.objects.get()
        self.assertFalse(message.is_staff_only)
        self.assertEqual(message.sender_type, "customer")
        self.assertContains(
            self.client.get(reverse("support:request_detail", args=[support_request.pk])),
            "Please check my order",
        )

    def test_staff_filters_replies_records_actor_and_requires_resolution_to_close(self):
        support_request = SupportRequest.objects.create(customer=self.customer, order=self.order)
        self.client.force_login(self.employee.user)

        response = self.client.get(reverse("support:staff_request_list"), {"status": "open", "order": self.order.pk})
        self.assertContains(response, f"Conversation #{support_request.pk}")
        self.assertNotContains(
            self.client.get(reverse("support:staff_request_list"), {"status": "closed"}),
            f"Conversation #{support_request.pk}",
        )

        response = self.client.post(
            reverse("support:staff_request_detail", args=[support_request.pk]),
            {"action": "message", "body": "We are investigating"},
        )
        self.assertRedirects(response, reverse("support:staff_request_detail", args=[support_request.pk]))
        reply = SupportMessage.objects.get()
        self.assertEqual(reply.employee, self.employee)
        self.assertIsNotNone(reply.created_at)

        response = self.client.post(
            reverse("support:staff_request_detail", args=[support_request.pk]),
            {"action": "status", "status": "closed", "resolution_note": ""},
        )
        self.assertEqual(response.status_code, 200)
        support_request.refresh_from_db()
        self.assertEqual(support_request.status, "open")

        self.client.post(
            reverse("support:staff_request_detail", args=[support_request.pk]),
            {"action": "status", "status": "closed", "resolution_note": "Replacement arranged"},
        )
        support_request.refresh_from_db()
        self.assertEqual(support_request.status, "closed")
        self.assertEqual(support_request.resolution_note, "Replacement arranged")
        self.assertEqual(StaffAction.objects.get().employee, self.employee)

    def test_staff_only_note_is_hidden_from_customer_and_customer_cannot_read_other_request(self):
        support_request = SupportRequest.objects.create(customer=self.customer, order=self.order)
        SupportMessage.objects.create(
            request=support_request,
            sender_type="employee",
            employee=self.employee,
            body="Public reply",
        )
        SupportMessage.objects.create(
            request=support_request,
            sender_type="employee",
            employee=self.employee,
            is_staff_only=True,
            body="Refund approved internally",
        )
        self.client.force_login(self.customer.user)

        response = self.client.get(reverse("support:request_detail", args=[support_request.pk]))

        self.assertContains(response, "Public reply")
        self.assertNotContains(response, "Refund approved internally")
        other_user = User.objects.create_user(username="other", password="safe-pass-123")
        Customer.objects.create(user=other_user, name="Other", email="other@example.com")
        self.assertEqual(
            self.client.get(reverse("support:request_detail", args=[support_request.pk])).status_code,
            200,
        )
        self.client.force_login(other_user)
        self.assertEqual(
            self.client.get(reverse("support:request_detail", args=[support_request.pk])).status_code,
            404,
        )
