from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import Customer, StaffAction
from accounts.views import staff_required
from orders.models import Order, PurchasedPC
from .forms import CustomerMessageForm, ReviewForm, SupportFilterForm, SupportMessageForm, SupportRequestForm, SupportStatusForm
from .models import Review, SupportMessage, SupportRequest


def review_list(request):
    reviews = Review.objects.select_related("purchased_pc__order_line").order_by("-created_at", "-pk")
    page = Paginator(reviews, 12).get_page(request.GET.get("page"))
    return render(request, "support/review_list.html", {"page_obj": page})


def review_detail(request, pk):
    review = get_object_or_404(Review.objects.select_related("purchased_pc__order_line"), pk=pk)
    return render(request, "support/review_detail.html", {"review": review})


@login_required
@require_http_methods(["GET", "POST"])
def review_create(request, pk):
    pc = get_object_or_404(PurchasedPC.objects.select_related("order_line__order"),
                          pk=pk, order_line__order__customer__user=request.user)
    order = pc.order_line.order
    if Review.objects.filter(purchased_pc=pc).exists():
        messages.info(request, "You have already reviewed this PC.")
        return redirect("orders:order_detail", pk=order.pk)
    if not pc.review_eligible:
        messages.error(request, "Only a delivered PC that has not been cancelled or refunded can be reviewed.")
        return redirect("orders:order_detail", pk=order.pk)
    form = ReviewForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Recheck eligibility at submission.
            order = get_object_or_404(Order, pk=order.pk, customer__user=request.user)
            pc = get_object_or_404(PurchasedPC.objects.select_related("order_line__order"), pk=pk)
            if not pc.review_eligible:
                messages.error(request, "This PC is no longer eligible for a review.")
                return redirect("orders:order_detail", pk=order.pk)
            line = pc.order_line
            snapshot = {
                "name": line.name,
                "components": pc.approved_parts if line.custom_snapshot.get("components") else [],
                "source_preset_name": line.custom_snapshot.get("source_preset_name", line.name if line.configuration_id else ""),
                "customised_from_preset": line.custom_snapshot.get("customised_from_preset", False),
                "has_substitutions": pc.substitutions.filter(status="accepted").exists(),
            }
            review, created = Review.objects.get_or_create(purchased_pc=pc, defaults={
                "customer": order.customer, "rating": form.cleaned_data["rating"],
                "text": form.cleaned_data["text"], "build_snapshot": snapshot,
            })
        if created:
            messages.success(request, "Your review has been published.")
        else:
            messages.info(request, "You have already reviewed this PC.")
        return redirect("support:review_detail", pk=review.pk)
    return render(request, "support/review_form.html", {"pc": pc, "form": form})


@staff_required
def staff_request_list(request):
    requests = SupportRequest.objects.select_related("customer", "order").order_by("-created_at", "-pk")
    form = SupportFilterForm(request.GET or None)
    if form.is_bound:
        if form.is_valid():
            status = form.cleaned_data["status"]
            order_id = form.cleaned_data["order"]
            if status:
                requests = requests.filter(status=status)
            if order_id is not None:
                requests = requests.filter(order_id=order_id)
        else:
            requests = requests.none()
    return render(request, "support/staff_list.html", {"requests": requests, "form": form})


@staff_required
@require_http_methods(["GET", "POST"])
def staff_request_detail(request, pk):
    support_request = get_object_or_404(
        SupportRequest.objects.select_related("customer", "order").prefetch_related("messages__employee__user"),
        pk=pk,
    )
    action = request.POST.get("action") if request.method == "POST" else None
    message_form = SupportMessageForm(request.POST if action == "message" else None)
    status_form = SupportStatusForm(
        request.POST if action == "status" else None,
        instance=support_request,
    )
    if request.method == "POST":
        with transaction.atomic():
            locked = get_object_or_404(SupportRequest, pk=pk)
            if action == "message" and message_form.is_valid():
                staff_only = message_form.cleaned_data["is_staff_only"]
                if locked.status == "closed" and not staff_only:
                    messages.error(request, "Reopen this conversation before sending a public reply.")
                    return redirect("support:staff_request_detail", pk=pk)
                SupportMessage.objects.create(
                    request=locked,
                    sender_type="employee",
                    employee=request.user.employee_profile,
                    is_staff_only=staff_only,
                    body=message_form.cleaned_data["body"],
                )
                messages.success(request, "Reply or staff note recorded.")
                return redirect("support:staff_request_detail", pk=pk)
            if action == "status" and status_form.is_valid():
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
        with transaction.atomic():
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
        SupportRequest.objects.select_related("order").prefetch_related(
            Prefetch("messages", queryset=SupportMessage.objects.filter(is_staff_only=False).order_by("created_at", "pk"),
                     to_attr="public_messages")
        ),
        pk=pk,
        customer=customer,
    )
    form = CustomerMessageForm(request.POST) if request.method == "POST" else CustomerMessageForm()
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked = get_object_or_404(SupportRequest, pk=pk, customer=customer)
            if locked.status == "closed":
                messages.error(request, "This conversation is closed.")
            else:
                SupportMessage.objects.create(
                    request=locked,
                    sender_type="customer",
                    body=form.cleaned_data["body"],
                )
                messages.success(request, "Message sent.")
        return redirect("support:request_detail", pk=pk)
    return render(request, "support/customer_detail.html", {"support_request": support_request, "form": form})
