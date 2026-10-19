from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import Customer, StaffAction
from accounts.views import staff_required
from .forms import CustomerMessageForm, SupportMessageForm, SupportRequestForm, SupportStatusForm
from .models import SupportMessage, SupportRequest


def placeholder(request, *args, **kwargs):
    return render(request, "support/placeholder.html")


@staff_required
def staff_request_list(request):
    requests = SupportRequest.objects.select_related("customer", "order").order_by("-created_at", "-pk")
    status = request.GET.get("status", "").strip()
    order_id = request.GET.get("order", "").strip()
    if status in {"open", "in_progress", "closed"}:
        requests = requests.filter(status=status)
    if order_id.isdecimal():
        requests = requests.filter(order_id=int(order_id))
    return render(request, "support/staff_list.html", {"requests": requests, "status": status, "order_id": order_id})


@staff_required
@require_http_methods(["GET", "POST"])
def staff_request_detail(request, pk):
    support_request = get_object_or_404(
        SupportRequest.objects.select_related("customer", "order").prefetch_related("messages__employee__user"),
        pk=pk,
    )
    message_form = SupportMessageForm(request.POST if request.method == "POST" and request.POST.get("action") == "message" else None)
    status_form = SupportStatusForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "status" else None,
        instance=support_request,
    )
    if request.method == "POST":
        with transaction.atomic():
            locked = get_object_or_404(SupportRequest.objects.select_for_update(), pk=pk)
            if request.POST.get("action") == "message" and message_form.is_valid():
                SupportMessage.objects.create(
                    request=locked,
                    sender_type="employee",
                    employee=request.user.employee_profile,
                    is_staff_only=message_form.cleaned_data["is_staff_only"],
                    body=message_form.cleaned_data["body"],
                )
                messages.success(request, "Reply or staff note recorded.")
                return redirect("support:staff_request_detail", pk=pk)
            if request.POST.get("action") == "status" and status_form.is_valid():
                old_status = locked.status
                new_status = status_form.cleaned_data["status"]
                locked.status = new_status
                locked.resolution_note = status_form.cleaned_data["resolution_note"]
                locked.save(update_fields=("status", "resolution_note"))
                if old_status != new_status:
                    StaffAction.objects.create(
                        employee=request.user.employee_profile,
                        action_type="support_status_changed",
                        object_ref=str(locked.pk),
                        detail=f"Support conversation changed from {old_status} to {new_status}.",
                    )
                messages.success(request, "Conversation status updated.")
                return redirect("support:staff_request_detail", pk=pk)
        messages.error(request, "Check the fields and try again.")
    return render(
        request,
        "support/staff_detail.html",
        {"support_request": support_request, "message_form": message_form, "status_form": status_form},
    )


@login_required
def request_list(request):
    customer = get_object_or_404(Customer, user=request.user)
    requests = customer.support_requests.select_related("order").order_by("-created_at", "-pk")
    return render(request, "support/customer_list.html", {"requests": requests})


@login_required
@require_http_methods(["GET", "POST"])
def request_create(request):
    customer = get_object_or_404(Customer, user=request.user)
    form = SupportRequestForm(request.POST if request.method == "POST" else None, customer=customer)
    if request.method == "POST" and form.is_valid():
        support_request = SupportRequest.objects.create(
            customer=customer, order=form.cleaned_data["order"]
        )
        SupportMessage.objects.create(
            request=support_request,
            sender_type="customer",
            body=form.cleaned_data["body"],
        )
        messages.success(request, "Support request submitted.")
        return redirect("support:request_detail", pk=support_request.pk)
    return render(request, "support/request_form.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def request_detail(request, pk):
    customer = get_object_or_404(Customer, user=request.user)
    support_request = get_object_or_404(
        SupportRequest.objects.select_related("order").prefetch_related("messages"),
        pk=pk,
        customer=customer,
    )
    form = CustomerMessageForm(request.POST) if request.method == "POST" else CustomerMessageForm()
    if request.method == "POST" and form.is_valid() and support_request.status != "closed":
        SupportMessage.objects.create(
            request=support_request,
            sender_type="customer",
            body=form.cleaned_data["body"],
        )
        messages.success(request, "Message sent.")
        return redirect("support:request_detail", pk=pk)
    return render(request, "support/customer_detail.html", {"support_request": support_request, "form": form})
