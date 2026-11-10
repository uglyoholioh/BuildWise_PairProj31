"""Run with --settings=buildwise.sqlite_test_settings to test real SQLite locks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipIf

from django.conf import settings
from django.contrib.auth.models import User
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TransactionTestCase
from django.contrib.messages.storage.fallback import FallbackStorage

from accounts.models import Customer, Employee
from accounts.views import employee_update
from catalogue.models import Accessory, Component
from inventory.models import Reservation, StockAdjustment
from .models import Order, OrderLine, PurchasedPC
from .views import _checkout_quote, checkout, order_cancel, start_order


class RequestSession(dict):
    """Keep request/session writes out of the inventory race under test."""
    modified = False


@skipIf(not settings.DATABASES['default'].get('TEST', {}).get('NAME'), 'Use file-backed sqlite_test_settings for concurrency tests')
class SQLiteConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.employee = Employee.objects.create(user=User.objects.create_user('staff'), role='manager')
        self.customers = [Customer.objects.create(user=User.objects.create_user(f'buyer{i}'), name=f'Buyer {i}',
                                                  email=f'buyer{i}@example.test') for i in range(2)]

    def request(self, user, data=None, cart=None):
        request = RequestFactory().post('/', data or {})
        request.user = User.objects.get(pk=user.pk)
        request.session = RequestSession(cart=cart or [])
        request._messages = FallbackStorage(request)
        return request

    def race(self, actions):
        barrier = Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            futures = [executor.submit(run, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def test_two_checkouts_for_last_accessory_do_not_oversell(self):
        self.assertEqual(connection.vendor, 'sqlite')
        accessory = Accessory.objects.create(name='Last mouse', category='Mouse', price=20)
        StockAdjustment.objects.create(item_type='accessory', accessory=accessory, delta=1,
                                       reason='Last unit', created_by=self.employee)
        def buy(customer):
            request = self.request(customer.user, {'name': customer.name, 'delivery_address': 'Test road'},
                                   [{'id': f'cart-{customer.pk}', 'kind': 'accessory', 'accessory_id': accessory.pk,
                                     'name': accessory.name, 'quantity': 1}])
            request.POST = request.POST.copy()
            request.POST["quote"] = _checkout_quote(request.user.customer_profile, [{
                "line": request.session["cart"][0], "items": [], "price": "20.00",
            }])
            return checkout(request).status_code
        self.assertEqual(self.race([lambda: buy(self.customers[0]), lambda: buy(self.customers[1])]), [302, 302])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Reservation.objects.filter(accessory=accessory, status='reserved').count(), 1)

    def test_concurrent_duplicate_checkout_creates_one_order(self):
        accessory = Accessory.objects.create(name="Mouse", category="Mouse", price=20)
        StockAdjustment.objects.create(item_type="accessory", accessory=accessory, delta=5,
                                       reason="Stock for duplicate test", created_by=self.employee)
        def buy():
            request = self.request(self.customers[0].user, {"name": "Buyer", "delivery_address": "Test road"},
                                   [{"id": "same-cart-line", "kind": "accessory", "accessory_id": accessory.pk,
                                     "name": accessory.name, "quantity": 1}])
            request.POST = request.POST.copy()
            request.POST["quote"] = _checkout_quote(request.user.customer_profile, [{
                "line": request.session["cart"][0], "items": [], "price": "20.00",
            }])
            return checkout(request).status_code
        self.assertEqual(self.race([buy, buy]), [302, 302])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Reservation.objects.filter(accessory=accessory, status="reserved").count(), 1)

    def test_cancel_and_start_cannot_leave_assembling_pc_without_reservation(self):
        cpu = Component.objects.create(category=Component.Category.CPU, brand='Example', model='CPU', socket='AM5', price=100)
        order = Order.objects.create(customer=self.customers[0], payment_status='paid', total_snapshot=100,
                                     delivery_name='Buyer', delivery_address='Test road')
        line = OrderLine.objects.create(order=order, kind='build', name='PC', unit_price_snapshot=100,
                                        custom_snapshot={'components': [{'component_id': cpu.pk, 'quantity': 1}]})
        pc = PurchasedPC.objects.create(order_line=line, unit_index=1)
        allocation = Reservation.objects.create(component=cpu, purchased_pc=pc, quantity=1)
        responses = self.race([
            lambda: order_cancel(self.request(self.customers[0].user), order.pk).status_code,
            lambda: start_order(self.request(self.employee.user), pc.pk).status_code,
        ])
        self.assertEqual(responses, [302, 302])
        order.refresh_from_db()
        pc.refresh_from_db()
        allocation.refresh_from_db()
        self.assertIn((order.status, pc.status, allocation.status), [
            ('cancelled', 'cancelled', 'released'), ('in_progress', 'assembling', 'reserved'),
        ])

    def test_concurrent_manager_demotions_leave_one_active_manager(self):
        other = Employee.objects.create(user=User.objects.create_user('manager2'), role='manager')
        def demote(employee):
            return employee_update(self.request(employee.user, {'role': 'employee', 'active': 'on'}), employee.pk).status_code
        responses = self.race([lambda: demote(self.employee), lambda: demote(other)])
        self.assertEqual(sorted(responses), [200, 302])
        self.assertEqual(Employee.objects.filter(role='manager', active=True, user__is_active=True).count(), 1)
