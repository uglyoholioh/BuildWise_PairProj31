from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import StaffAction
from .forms import ComponentForm
from .models import Component


def placeholder(request, *args, **kwargs):
    return HttpResponse("Page under construction")


def component_list(request):
    return render(
        request,
        "catalogue/component_list.html",
        {"components": Component.objects.order_by("category", "brand", "model")},
    )


@require_http_methods(["GET", "POST"])
def component_create(request):
    form = ComponentForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            component = form.save()
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="component_created",
                object_ref=str(component.pk),
                detail=f"Created {component.category}: {component.brand} {component.model}.",
            )
        messages.success(request, "Component created.")
        return redirect("catalogue:component_list")
    return render(request, "catalogue/component_form.html", {"form": form, "title": "Add component"})


@require_http_methods(["GET", "POST"])
def component_update(request, pk):
    component = get_object_or_404(Component, pk=pk)
    old_active = component.active
    form = ComponentForm(request.POST if request.method == "POST" else None, instance=component)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            component = form.save()
            if old_active != component.active:
                action_type = "component_deactivated" if not component.active else "component_reactivated"
            else:
                action_type = "component_updated"
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type=action_type,
                object_ref=str(component.pk),
                detail=f"Updated {component.category}: {component.brand} {component.model}.",
            )
        messages.success(request, "Component updated.")
        return redirect("catalogue:component_list")
    return render(
        request,
        "catalogue/component_form.html",
        {"form": form, "title": f"Edit {component.brand} {component.model}"},
    )
