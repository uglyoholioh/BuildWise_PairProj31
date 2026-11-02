from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Customer, Employee


class LoginRoutingTests(TestCase):
    def test_protected_customer_routes_use_named_login_and_preserve_next(self):
        for path in ('/checkout', '/accounts/profile', '/orders', '/support', '/substitutions/1/decision'):
            response = self.client.post(path) if 'decision' in path else self.client.get(path)
            self.assertRedirects(response, reverse('accounts:login') + '?next=' + path,
                                 fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('accounts:login')).status_code, 200)

    def test_staff_login_fallback_safe_next_and_unsafe_next(self):
        user = User.objects.create_user(username='staff', password='safe-pass-123')
        Employee.objects.create(user=user)
        for target, expected in [('', reverse('inventory:inventory_dashboard')),
                                 ('/staff/queue', '/staff/queue'),
                                 ('https://example.org/', reverse('inventory:inventory_dashboard'))]:
            self.client.logout()
            response = self.client.post(reverse('staff_login'), {
                'username': 'staff', 'password': 'safe-pass-123', 'next': target,
            })
            self.assertRedirects(response, expected)

    def test_staff_login_rejects_customer_and_inactive_employee(self):
        user = User.objects.create_user(username='buyer', password='safe-pass-123')
        Customer.objects.create(user=user, name='Buyer', email='buyer@example.test')
        for employee_active, user_active in [(None, True), (False, True), (True, False)]:
            if employee_active is not None:
                Employee.objects.update_or_create(user=user, defaults={'active': employee_active})
            user.is_active = user_active
            user.save()
            response = self.client.post(reverse('staff_login'), {'username': 'buyer', 'password': 'safe-pass-123'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('_auth_user_id', self.client.session)

    def test_home_uses_storefront(self):
        self.assertTemplateUsed(self.client.get('/'), 'catalogue/preset_list.html')


class EmployeeManagementTests(TestCase):
    def setUp(self):
        self.manager = Employee.objects.create(user=User.objects.create_user('manager'), role=Employee.Role.MANAGER)
        self.employee = Employee.objects.create(user=User.objects.create_user('employee'))
        self.client.force_login(self.manager.user)

    def test_only_active_managers_can_read_or_change_employees(self):
        buyer = User.objects.create_user('buyer')
        Customer.objects.create(user=buyer, name='Buyer', email='buyer@example.test')
        for user in (buyer, self.employee.user):
            self.client.force_login(user)
            for url in (reverse('employee_list'), reverse('employee_create'), reverse('employee_update', args=[self.manager.pk])):
                self.assertIn(self.client.get(url).status_code, (302, 403))
                self.assertIn(self.client.post(url, {'role': 'manager', 'active': 'on'}).status_code, (302, 403))
        self.manager.active = False
        self.manager.save()
        self.client.force_login(self.manager.user)
        self.assertEqual(self.client.get(reverse('employee_list')).status_code, 302)

    def test_creation_hashes_password_and_logs_role_without_granting_django_admin(self):
        from .models import StaffAction
        data = {'username': 'newstaff', 'role': 'manager', 'password1': 'BuildWise!TestPass482',
                'password2': 'BuildWise!TestPass482', 'is_superuser': 'on', 'is_staff': 'on'}
        response = self.client.post(reverse('employee_create'), data)
        self.assertRedirects(response, reverse('employee_list'))
        user = User.objects.get(username='newstaff')
        self.assertTrue(user.check_password(data['password1']))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.employee_profile.role, 'manager')
        self.assertFalse(Customer.objects.filter(user=user).exists())
        self.assertTrue(StaffAction.objects.filter(action_type='employee_created', employee=self.manager).exists())
        self.assertEqual(self.client.post(reverse('employee_create'), data).status_code, 200)
        self.assertEqual(Employee.objects.count(), 3)

    def test_final_active_manager_cannot_be_demoted_or_deactivated(self):
        Employee.objects.create(user=User.objects.create_user('inactive'), role='manager', active=False)
        for data in ({'role': 'employee', 'active': 'on'}, {'role': 'manager'}):
            response = self.client.post(reverse('employee_update', args=[self.manager.pk]), data)
            self.assertContains(response, 'Keep at least one active manager')
            self.manager.refresh_from_db()
            self.assertEqual(self.manager.role, 'manager')
            self.assertTrue(self.manager.active)

    def test_deactivation_blocks_existing_session_preserves_attribution_and_is_idempotent(self):
        from django.test import Client
        from .models import StaffAction
        record = StaffAction.objects.create(employee=self.employee, action_type='historical', detail='Existing action')
        staff_client = Client()
        staff_client.force_login(self.employee.user)
        url = reverse('employee_update', args=[self.employee.pk])
        self.assertRedirects(self.client.post(url, {'role': 'employee'}), reverse('employee_list'))
        self.client.post(url, {'role': 'employee'})
        self.employee.refresh_from_db()
        self.employee.user.refresh_from_db()
        self.assertFalse(self.employee.active)
        self.assertFalse(self.employee.user.is_active)
        self.assertEqual(staff_client.get(reverse('orders:order_queue')).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.employee_id, self.employee.pk)
        self.assertEqual(StaffAction.objects.filter(action_type='employee_updated').count(), 1)
        self.client.post(url, {'role': 'manager', 'active': 'on'})
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.active)
        self.assertEqual(self.employee.role, 'manager')
        self.assertRedirects(self.client.post(reverse('employee_update', args=[self.manager.pk]), {'role': 'employee', 'active': 'on'}), reverse('employee_list'), fetch_redirect_response=False)

    def test_customer_registration_cannot_grant_employee_privileges(self):
        self.client.logout()
        response = self.client.post(reverse('accounts:register'), {
            'username': 'customer', 'name': 'Customer', 'email': 'customer@example.test',
            'password1': 'BuildWise!TestPass482', 'password2': 'BuildWise!TestPass482',
            'role': 'manager', 'active': 'on', 'is_staff': 'on', 'is_superuser': 'on',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='customer')
        self.assertFalse(Employee.objects.filter(user=user).exists())
        self.assertFalse(user.is_staff or user.is_superuser)
