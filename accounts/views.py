from django.contrib.auth import logout
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Customer, Employee


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


@staff_required
def customer_list(request):
    query = request.GET.get("q", "").strip()
    customers = Customer.objects.order_by("name", "pk")
    if query:
        filters = Q(name__icontains=query) | Q(email__icontains=query)
        if query.isdecimal():
            filters |= Q(pk=int(query))
        customers = customers.filter(filters)
    return render(request, "accounts/customer_list.html", {"customers": customers, "query": query})


@staff_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    orders = customer.orders.select_related("shipment").prefetch_related(
        "lines__pcs", "status_history__employee__user", "support_requests"
    ).order_by("-placed_at", "-pk")
    return render(
        request,
        "accounts/customer_detail.html",
        {"customer": customer, "orders": orders},
    )
