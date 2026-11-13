from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import StaffAction
from accounts.views import staff_required
from catalogue.models import Accessory, Component
from .availability import stock_position
from .forms import CancelledStockInspectionForm, StockAdjustmentForm
from .models import Reservation, StockAdjustment


@staff_required
def inventory_dashboard(request):
    components = [
        {"item": item, **stock_position("component", item)}
        for item in Component.objects.order_by("category", "brand", "model")
    ]
    accessories = [
        {"item": item, **stock_position("accessory", item)}
        for item in Accessory.objects.order_by("category", "name")
    ]
    adjustments = StockAdjustment.objects.select_related(
        "component", "accessory", "created_by__user"
    ).order_by("-created_at", "-pk")[:30]
    inspections = Reservation.objects.filter(
        status=Reservation.Status.RESERVED, purchased_pc__status="cancelled",
        component__isnull=False,
    ).select_related("component", "purchased_pc__order_line").order_by("pk")
    return render(
        request,
        "inventory/dashboard.html",
        {"components": components, "accessories": accessories, "adjustments": adjustments,
         "inspections": inspections},
    )


@staff_required
@require_POST
def inspect_cancelled_stock(request, pk):
    form = CancelledStockInspectionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a positive quantity and choose an inspection outcome.")
        return redirect("inventory:inventory_dashboard")

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects, pk=pk, component__isnull=False,
            purchased_pc__status="cancelled", status=Reservation.Status.RESERVED,
        )
        quantity = form.cleaned_data["quantity"]
        if quantity > reservation.quantity:
            messages.error(request, "The inspected quantity exceeds the reserved quantity.")
            return redirect("inventory:inventory_dashboard")
        employee = request.user.employee_profile
        discard = form.cleaned_data["outcome"] == "discard"
        if discard:
            StockAdjustment.objects.create(
                item_type="component", component=reservation.component, delta=-quantity,
                reason=f"Unusable stock from cancelled PC #{reservation.purchased_pc_id}.",
                created_by=employee,
            )
        if quantity < reservation.quantity:
            Reservation.objects.create(
                component=reservation.component, purchased_pc=reservation.purchased_pc,
                quantity=reservation.quantity - quantity,
            )
        reservation.quantity = quantity
        reservation.status = Reservation.Status.CONSUMED if discard else Reservation.Status.RELEASED
        reservation.save(update_fields=("quantity", "status"))
        StaffAction.objects.create(
            employee=employee, action_type="cancelled_stock_inspected",
            object_ref=str(reservation.pk),
            detail=f"PC #{reservation.purchased_pc_id}: {quantity} × {reservation.component} "
                   f"{'written off' if discard else 'returned to available stock'}.",
        )
    messages.success(request, "Inspection recorded.")
    return redirect("inventory:inventory_dashboard")


@staff_required
@require_http_methods(["GET", "POST"])
def adjustment_create(request):
    form = StockAdjustmentForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            item_type = form.cleaned_data["item_type"]
            item = form.cleaned_data["component"] or form.cleaned_data["accessory"]
            item_model = Component if item_type == "component" else Accessory
            locked_item = get_object_or_404(item_model, pk=item.pk)
            current = stock_position(item_type, locked_item)
            delta = form.cleaned_data["delta"]
            if delta < 0 and -delta > current["available"]:
                form.add_error(
                    "delta",
                    f"Only {current['available']} units are available after reservations.",
                )
            else:
                adjustment = form.save(commit=False)
                if item_type == "component":
                    adjustment.component, adjustment.accessory = locked_item, None
                else:
                    adjustment.accessory, adjustment.component = locked_item, None
                adjustment.created_by = request.user.employee_profile
                adjustment.save()
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="stock_adjusted",
                    object_ref=str(adjustment.pk),
                    detail=f"Changed {item_type} stock by {delta}: {adjustment.reason}",
                )
                messages.success(request, "Stock adjustment recorded.")
                return redirect("inventory:inventory_dashboard")
    return render(request, "inventory/adjustment_form.html", {"form": form})
