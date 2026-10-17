from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import StaffAction
from inventory.availability import available_component_quantity
from inventory.models import Reservation, StockAdjustment
from .allocations import accessory_allocation_issues, purchased_pc_allocation_issues
from .forms import HoldPCForm, QualityCheckForm
from .models import Order, OrderLine, OrderStatusHistory, PurchasedPC, QualityCheck


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
                purchased_pc.allocation_issues = (
                    [] if purchased_pc.status == "quality_passed"
                    else purchased_pc_allocation_issues(purchased_pc)
                )
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
def start_quality_check(request, pk):
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=pk)
        if order.status in ("cancelled", "delivered") or pc.status != "assembling":
            messages.error(request, "This PC is not ready for quality checks.")
        else:
            pc.status = "quality_checking"
            pc.save(update_fields=("status",))
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="pc_quality_check_started",
                object_ref=str(pc.pk),
                detail=f"Started quality checks for PC #{pc.pk}.",
            )
            messages.success(request, f"Quality checks started for PC #{pc.pk}.")
    return redirect("orders:order_queue")


@require_POST
def record_quality_check(request, pk):
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=pk)
        form = QualityCheckForm(request.POST, purchased_pc=pc)
        if not form.is_valid():
            messages.error(request, "Choose a check, and include a reason when it fails.")
        elif order.status in ("cancelled", "delivered") or pc.status not in (
            "quality_checking", "corrective_work"
        ):
            messages.error(request, "This PC is not accepting quality checks.")
        else:
            passed = form.cleaned_data["passed"] == "yes"
            note = form.cleaned_data["note"].strip()
            check = QualityCheck.objects.create(
                purchased_pc=pc,
                employee=request.user.employee_profile,
                stage="quality_checking",
                checklist_item=form.cleaned_data["checklist_item"],
                passed=passed,
                note=note,
            )
            faulty = form.cleaned_data["faulty_component"]
            if not passed:
                pc.status = "on_hold"
                pc.hold_reason = note
                if faulty:
                    reservations = Reservation.objects.select_for_update().filter(
                        purchased_pc=pc,
                        component=faulty,
                        status=Reservation.Status.RESERVED,
                    ).order_by("pk")
                    reservation = reservations.first()
                    if reservation:
                        available = available_component_quantity(faulty)
                        if available < 1:
                            if reservation.quantity == 1:
                                reservation.status = Reservation.Status.RELEASED
                                reservation.save(update_fields=("status",))
                            else:
                                reservation.quantity -= 1
                                reservation.save(update_fields=("quantity",))
                        StockAdjustment.objects.create(
                            item_type="component",
                            component=faulty,
                            delta=-1,
                            reason=f"Faulty part removed from PC #{pc.pk}: {note}",
                            created_by=request.user.employee_profile,
                        )
                        if available > 0:
                            pc.status = "corrective_work"
                            pc.hold_reason = "A working replacement is reserved; retest this check."
                        else:
                            pc.hold_reason = f"{note} No working unit is available; use a US-A11 substitution."
                pc.save(update_fields=("status", "hold_reason"))
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="pc_quality_check_failed",
                    object_ref=str(pc.pk),
                    detail=f"{dict(QualityCheckForm.CHECKS)[check.checklist_item]}: {pc.hold_reason}",
                )
                messages.error(request, f"PC #{pc.pk} needs corrective work: {pc.hold_reason}")
            else:
                latest = {}
                for row in pc.quality_checks.order_by("created_at", "pk"):
                    latest[row.checklist_item] = row.passed
                required = {key for key, _ in QualityCheckForm.CHECKS}
                if required <= latest.keys() and all(latest[key] for key in required):
                    for reservation in Reservation.objects.select_for_update().filter(
                        purchased_pc=pc, status=Reservation.Status.RESERVED, component__isnull=False
                    ).select_related("component"):
                        StockAdjustment.objects.create(
                            item_type="component",
                            component=reservation.component,
                            delta=-reservation.quantity,
                            reason=f"PC #{pc.pk} passed quality checks; components installed.",
                            created_by=request.user.employee_profile,
                        )
                        reservation.status = Reservation.Status.CONSUMED
                        reservation.save(update_fields=("status",))
                    pc.status = "quality_passed"
                    pc.hold_reason = ""
                    pc.save(update_fields=("status", "hold_reason"))
                    StaffAction.objects.create(
                        employee=request.user.employee_profile,
                        action_type="pc_quality_passed",
                        object_ref=str(pc.pk),
                        detail=f"PC #{pc.pk} passed all quality checks; reserved components consumed.",
                    )
                    messages.success(request, f"PC #{pc.pk} passed quality checks.")
                elif pc.status == "corrective_work":
                    pc.status = "quality_checking"
                    pc.save(update_fields=("status",))
                    messages.success(request, "Check recorded; continue the remaining tests.")
                else:
                    messages.success(request, "Check recorded.")
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
