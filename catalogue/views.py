from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import Employee, StaffAction
from inventory.availability import available_accessory_quantity
from .compatibility import check_configuration_components, revalidate_configurations
from .forms import (
    AccessoryForm,
    AssemblyFeeForm,
    ComponentConflictForm,
    ComponentForm,
    ConfigurationForm,
    ConfigurationItemFormSet,
)
from .models import Accessory, AssemblyFeeSetting, Component, ComponentConflict, Configuration
from .pricing import available_preset_quantity, calculate_pc_price


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
def accessory_manage_list(request):
    accessories = [
        {"item": item, "available": available_accessory_quantity(item)}
        for item in Accessory.objects.order_by("category", "name")
    ]
    return render(request, "catalogue/accessory_manage_list.html", {"accessories": accessories})


@require_http_methods(["GET", "POST"])
def accessory_create(request):
    form = AccessoryForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            accessory = form.save()
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="accessory_created",
                object_ref=str(accessory.pk),
                detail=f"Created accessory {accessory.name} at ${accessory.price}.",
            )
        messages.success(request, "Accessory created.")
        return redirect("catalogue:accessory_manage_list")
    return render(request, "catalogue/accessory_form.html", {"form": form, "title": "Add accessory"})


@require_http_methods(["GET", "POST"])
def accessory_update(request, pk):
    accessory = get_object_or_404(Accessory, pk=pk)
    old_active = accessory.active
    form = AccessoryForm(request.POST if request.method == "POST" else None, instance=accessory)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            accessory = form.save()
            action_type = (
                "accessory_deactivated" if old_active and not accessory.active
                else "accessory_reactivated" if not old_active and accessory.active
                else "accessory_updated"
            )
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type=action_type,
                object_ref=str(accessory.pk),
                detail=f"Updated accessory {accessory.name} at ${accessory.price}.",
            )
        messages.success(request, "Accessory updated.")
        return redirect("catalogue:accessory_manage_list")
    return render(
        request,
        "catalogue/accessory_form.html",
        {"form": form, "title": f"Edit {accessory.name}"},
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


def current_assembly_fee():
    return AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()


def preset_list(request):
    fee = current_assembly_fee()
    presets = []
    for configuration in Configuration.objects.prefetch_related(
        "items__component"
    ).order_by("name"):
        items = list(configuration.items.all())
        issues = check_configuration_components(item.component for item in items)
        price = calculate_pc_price(items, fee.fee_amount) if fee and not issues else None
        presets.append(
            {
                "configuration": configuration,
                "price": price,
                "has_price": price is not None,
                "available_quantity": available_preset_quantity(items) if not issues else 0,
                "issues": issues,
            }
        )
    return render(
        request,
        "catalogue/preset_list.html",
        {
            "presets": presets,
            "assembly_fee": fee.fee_amount if fee else None,
            "has_fee": fee is not None,
        },
    )


def _edit_preset(request, configuration=None):
    creating = configuration is None
    configuration = configuration or Configuration()
    form = ConfigurationForm(
        request.POST if request.method == "POST" else None, instance=configuration
    )
    formset = ConfigurationItemFormSet(
        request.POST if request.method == "POST" else None,
        instance=configuration,
        prefix="items",
    )
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        items = [
            item_form.cleaned_data
            for item_form in formset.forms
            if item_form.cleaned_data
            and not item_form.cleaned_data.get("DELETE")
            and item_form.cleaned_data.get("component")
        ]
        components = [item["component"] for item in items]
        fee = current_assembly_fee()
        if form.cleaned_data["status"] == Configuration.Status.PUBLISHED:
            if fee is None:
                form.add_error("status", "Set the shared assembly fee before publishing a preset.")
            for issue in check_configuration_components(components):
                form.add_error(None, issue)

        if not form.errors:
            with transaction.atomic():
                configuration = form.save()
                formset.instance = configuration
                formset.save()
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="configuration_created" if creating else "configuration_updated",
                    object_ref=str(configuration.pk),
                    detail=f"Saved preset {configuration.name} with status {configuration.status}.",
                )
            messages.success(request, "Preset saved.")
            return redirect("catalogue:preset_manage_list")
    return render(
        request,
        "catalogue/preset_form.html",
        {"form": form, "formset": formset, "title": "Add preset" if creating else f"Edit {configuration.name}"},
    )


@require_http_methods(["GET", "POST"])
def preset_create(request):
    return _edit_preset(request)


@require_http_methods(["GET", "POST"])
def preset_update(request, pk):
    return _edit_preset(request, get_object_or_404(Configuration, pk=pk))


def active_manager(user):
    return (
        user.is_authenticated
        and user.is_active
        and Employee.objects.filter(
            user=user, active=True, role=Employee.Role.MANAGER
        ).exists()
    )


@user_passes_test(active_manager, login_url="staff_login")
@require_http_methods(["GET", "POST"])
def assembly_fee(request):
    fee = current_assembly_fee()
    form = AssemblyFeeForm(
        request.POST if request.method == "POST" else None,
        initial={"fee_amount": fee.fee_amount if fee else None},
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            fee = AssemblyFeeSetting.objects.create(
                fee_amount=form.cleaned_data["fee_amount"],
                updated_by=request.user.employee_profile,
            )
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="assembly_fee_updated",
                object_ref=str(fee.pk),
                detail=f"Set assembly and testing fee to {fee.fee_amount}.",
            )
        messages.success(request, "Shared assembly fee updated.")
        return redirect("catalogue:assembly_fee")
    return render(
        request, "catalogue/assembly_fee.html", {"form": form, "fee": fee}
    )
