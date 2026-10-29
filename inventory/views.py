from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import StaffAction
from catalogue.models import Accessory, Component
from .availability import stock_position
from .forms import StockAdjustmentForm
from .models import StockAdjustment


def placeholder(request, *args, **kwargs):
    return HttpResponse("Page under construction")


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
    return render(
        request,
        "inventory/dashboard.html",
        {"components": components, "accessories": accessories, "adjustments": adjustments},
    )


@require_http_methods(["GET", "POST"])
def adjustment_create(request):
    form = StockAdjustmentForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            item_type = form.cleaned_data["item_type"]
            item = form.cleaned_data["component"] or form.cleaned_data["accessory"]
            item_model = Component if item_type == "component" else Accessory
            locked_item = get_object_or_404(item_model.objects.select_for_update(), pk=item.pk)
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
