from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import StaffAction
from inventory.models import Reservation
from .allocations import accessory_allocation_issues, purchased_pc_allocation_issues
from .forms import HoldPCForm
from .models import Order, OrderLine, OrderStatusHistory, PurchasedPC


def placeholder(request, *args, **kwargs):
    return HttpResponse("Page under construction")


def order_queue(request):
    orders = list(
        Order.objects.filter(payment_status="paid")
        .exclude(status__in=("cancelled", "delivered"))
        .select_related("customer")
        .prefetch_related(
            "lines__pcs__reservations__component",
            "lines__pcs__reservations__accessory",
            "lines__configuration__items__component",
            "lines__accessory",
            "lines__reservations__component",
            "lines__reservations__accessory",
        )
        .order_by("placed_at", "pk")
    )
    for order in orders:
        lines = list(order.lines.all())
        order.is_accessory_only = bool(lines) and all(line.kind == "accessory" for line in lines)
        order.accessory_allocation_issues = accessory_allocation_issues(order) if order.is_accessory_only else []
        for line in lines:
            for purchased_pc in line.pcs.all():
                purchased_pc.allocation_issues = purchased_pc_allocation_issues(purchased_pc)
    return render(request, "orders/order_queue.html", {"orders": orders})


def _order_id_for_pc(pk):
    order_id = PurchasedPC.objects.filter(pk=pk).values_list("order_line__order_id", flat=True).first()
    if order_id is None:
        raise Http404
    return order_id


@login_required
@require_POST
def order_cancel(request, pk):
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(), pk=pk, customer__user=request.user
        )
        if order.status != "placed":
            messages.error(request, "This order can no longer be cancelled.")
        else:
            order.status = "cancelled"
            order.save(update_fields=("status",))
            OrderStatusHistory.objects.create(
                order=order,
                from_status="placed",
                to_status="cancelled",
                note="Customer cancelled before assembly started.",
            )
            messages.success(request, "Your order has been cancelled.")
    return redirect("orders:order_list")


@require_POST
def start_order(request, pk):
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(
            PurchasedPC.objects.select_for_update().select_related("order_line"), pk=pk
        )
        if order.payment_status != "paid" or order.status in ("cancelled", "delivered"):
            messages.error(request, "This order cannot be started.")
        elif pc.status not in ("queued", "on_hold"):
            messages.error(request, "This PC is no longer waiting to start.")
        else:
            issues = purchased_pc_allocation_issues(pc)
            if issues:
                pc.status = "on_hold"
                pc.hold_reason = " ".join(issues)
                pc.save(update_fields=("status", "hold_reason"))
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="pc_allocation_hold",
                    object_ref=str(pc.pk),
                    detail=pc.hold_reason,
                )
                messages.error(request, f"PC #{pc.pk} was put on hold: {pc.hold_reason}")
            else:
                pc.status = "assembling"
                pc.hold_reason = ""
                pc.save(update_fields=("status", "hold_reason"))
                if order.status == "placed":
                    order.status = "in_progress"
                    order.save(update_fields=("status",))
                    OrderStatusHistory.objects.create(
                        order=order,
                        from_status="placed",
                        to_status="in_progress",
                        note=f"Assembly started for PC #{pc.pk}.",
                        employee=request.user.employee_profile,
                    )
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="pc_assembly_started",
                    object_ref=str(pc.pk),
                    detail=f"Started assembly for PC #{pc.pk} in order #{order.pk}.",
                )
                messages.success(request, f"Assembly started for PC #{pc.pk}.")
    return redirect("orders:order_queue")


@require_POST
def hold_pc(request, pk):
    form = HoldPCForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a reason before placing the PC on hold.")
        return redirect("orders:order_queue")
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=pk)
        if (
            order.payment_status != "paid"
            or order.status in ("cancelled", "delivered")
            or pc.status not in ("queued", "assembling")
        ):
            messages.error(request, "This PC cannot be put on hold in its current state.")
        else:
            pc.status = "on_hold"
            pc.hold_reason = form.cleaned_data["reason"]
            pc.save(update_fields=("status", "hold_reason"))
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="pc_manual_hold",
                object_ref=str(pc.pk),
                detail=pc.hold_reason,
            )
            messages.success(request, f"PC #{pc.pk} is on hold.")
    return redirect("orders:order_queue")


@require_POST
def start_packing(request, pk):
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        lines = list(order.lines.all())
        if order.payment_status != "paid" or not lines or any(line.kind != "accessory" for line in lines):
            messages.error(request, "Only paid accessory-only orders can enter the packing queue.")
        elif order.status == "placed":
            issues = accessory_allocation_issues(order)
            if issues:
                messages.error(request, "Cannot start packing: " + " ".join(issues))
            else:
                order.status = "packing"
                order.save(update_fields=("status",))
                OrderStatusHistory.objects.create(
                    order=order,
                    from_status="placed",
                    to_status="packing",
                    note="Accessory-only order packing started.",
                    employee=request.user.employee_profile,
                )
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="accessory_packing_started",
                    object_ref=str(order.pk),
                    detail=f"Started packing accessory-only order #{order.pk}.",
                )
                messages.success(request, f"Packing started for order #{order.pk}.")
        else:
            messages.error(request, "This order is no longer waiting to be packed.")
    return redirect("orders:order_queue")
