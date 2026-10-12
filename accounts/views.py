from django.contrib.auth import logout
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .models import Employee


def active_employee(user):
    return (
        user.is_authenticated
        and user.is_active
        and Employee.objects.filter(
            user=user, active=True, role__in=Employee.Role.values
        ).exists()
    )


staff_required = user_passes_test(active_employee, login_url="staff_login")


class EmployeeAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not active_employee(user):
            raise ValidationError("Username or password is incorrect.", code="invalid_login")


class StaffLoginView(LoginView):
    template_name = "accounts/staff_login.html"
    authentication_form = EmployeeAuthenticationForm
    success_url = "/staff/"


@require_POST
def logout_view(request):
    logout(request)
    return redirect("/")


def placeholder(request, *args, **kwargs):
    return HttpResponse("Page under construction")
