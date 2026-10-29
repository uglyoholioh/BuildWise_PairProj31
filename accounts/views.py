from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Customer, Employee
from .forms import CustomerAuthenticationForm, CustomerProfileForm, CustomerRegistrationForm


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


class CustomerLoginView(LoginView):
    template_name = "accounts/customer_login.html"
    authentication_form = CustomerAuthenticationForm

    def get_success_url(self):
        return self.get_redirect_url() or reverse("accounts:profile")


def customer_register(request):
    form = CustomerRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        target = request.POST.get("next", "")
        with transaction.atomic():
            user = form.save()
        login(request, user)
        if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            return redirect(target)
        return redirect("accounts:profile")
    return render(request, "accounts/customer_register.html", {"form": form, "next": request.GET.get("next", "")})


@login_required
def customer_profile(request):
    customer = get_object_or_404(Customer, user=request.user)
    form = CustomerProfileForm(request.POST or None, instance=customer)
    password_form = PasswordChangeForm(request.user, request.POST or None) if request.POST.get("change_password") else PasswordChangeForm(request.user)
    for field in password_form.fields.values():
        field.widget.attrs["class"] = "form-control"
    if request.method == "POST":
        if request.POST.get("change_password") and password_form.is_valid():
            password_form.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Password changed.")
            return redirect("accounts:profile")
        if not request.POST.get("change_password") and form.is_valid():
            with transaction.atomic():
                customer = form.save()
                request.user.email = customer.email
                request.user.save(update_fields=("email",))
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    return render(
        request,
        "accounts/customer_profile.html",
        {"customer": customer, "form": form, "password_form": password_form},
    )


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
