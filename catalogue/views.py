from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import StaffAction
from .compatibility import revalidate_configurations
from .forms import ComponentConflictForm, ComponentForm
from .models import Component, ComponentConflict


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
            revalidate_configurations([component])
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


@require_http_methods(["GET", "POST"])
def compatibility_rules(request):
    form = ComponentConflictForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            conflict = form.save()
            revalidate_configurations([conflict.component_a, conflict.component_b])
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="component_conflict_added",
                object_ref=str(conflict.pk),
                detail=(
                    f"Marked {conflict.component_a} and {conflict.component_b} incompatible: "
                    f"{conflict.reason}"
                ),
            )
        messages.success(request, "Compatibility rule added.")
        return redirect("catalogue:compatibility_rules")
    return render(
        request,
        "catalogue/compatibility_rules.html",
        {
            "form": form,
            "conflicts": ComponentConflict.objects.select_related(
                "component_a", "component_b"
            ).order_by("component_a__category", "component_a__brand", "component_b__brand"),
        },
    )


@require_http_methods(["POST"])
def compatibility_rule_delete(request, pk):
    with transaction.atomic():
        conflict = get_object_or_404(
            ComponentConflict.objects.select_for_update().select_related(
                "component_a", "component_b"
            ),
            pk=pk,
        )
        component_a, component_b = conflict.component_a, conflict.component_b
        detail = f"Removed incompatibility between {component_a} and {component_b}."
        conflict.delete()
        revalidate_configurations([component_a, component_b])
        StaffAction.objects.create(
            employee=request.user.employee_profile,
            action_type="component_conflict_removed",
            object_ref=str(pk),
            detail=detail,
        )
    messages.success(request, "Compatibility rule removed.")
    return redirect("catalogue:compatibility_rules")
