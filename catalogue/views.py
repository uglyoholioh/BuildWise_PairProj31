from django.contrib import messages
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import Employee, StaffAction
from accounts.models import Customer
from accounts.views import active_manager
from inventory.availability import available_accessory_quantity
from .compatibility import check_compatibility, check_configuration_components, revalidate_configurations
from .forms import (
    AccessoryForm,
    AssemblyFeeForm,
    ComponentConflictForm,
    ComponentForm,
    ConfigurationForm,
    ConfigurationItemFormSet,
)
from .models import Accessory, AssemblyFeeSetting, Component, ComponentConflict, Configuration, ConfigurationItem
from .pricing import available_preset_quantity, calculate_pc_price
from inventory.availability import available_component_quantity


def placeholder(request, *args, **kwargs):
    return HttpResponse("Page under construction")


@require_http_methods(["GET", "POST"])
def configure(request):
    from recommendations.models import GameSettingSupport
    from recommendations.predictor import predict_temporary_fps

    session_build = request.session.get("current_build", {})
    preset_id = request.GET.get("preset") if request.method == "GET" else None
    if preset_id:
        preset = get_object_or_404(Configuration, pk=preset_id, status=Configuration.Status.PUBLISHED)
        session_build = {
            "name": preset.name,
            "preset_id": preset.pk,
            "components": [
                {"component_id": item.component_id, "quantity": item.quantity}
                for item in preset.items.all()
            ],
        }
    selected_ids = {row["component_id"]: row["quantity"] for row in session_build.get("components", [])}
    if request.method == "POST":
        previous_quantities = selected_ids
        selected_ids = {}
        for category, label in Component.Category.choices:
            raw_id = request.POST.get(f"component_{category}", "").strip()
            if raw_id:
                try:
                    component = Component.objects.get(pk=int(raw_id), category=category, active=True)
                except (ValueError, Component.DoesNotExist):
                    messages.error(request, f"Select an active {label.lower()}.")
                    return redirect("catalogue:configure")
                selected_ids[component.pk] = previous_quantities.get(component.pk, 1)
        session_build = {
            "name": request.POST.get("name", "My custom PC").strip()[:160] or "My custom PC",
            "preset_id": session_build.get("preset_id"),
            "components": [{"component_id": pk, "quantity": quantity} for pk, quantity in selected_ids.items()],
        }
    components = Component.objects.filter(pk__in=selected_ids)
    component_map = {component.pk: component for component in components}
    items = [ConfigurationItem(component=component_map[pk], quantity=quantity) for pk, quantity in selected_ids.items() if pk in component_map]
    issues = check_configuration_components(item.component for item in items)
    for component_id in selected_ids.keys() - component_map.keys():
        issues.append(f"Saved component #{component_id} is no longer in the catalogue. Choose a replacement or update the build to remove it.")
    fee = current_assembly_fee()
    price = calculate_pc_price(items, fee.fee_amount) if fee and not issues else None
    available = min((available_component_quantity(item.component) // item.quantity for item in items), default=0) if items and not issues else 0
    rows = []
    for category, label in Component.Category.choices:
        choices = list(Component.objects.filter(Q(active=True) | Q(pk__in=selected_ids), category=category).order_by("brand", "model"))
        other_components = [item.component for item in items if item.component.category != category]
        choices.sort(key=lambda component: bool(check_compatibility([*other_components, component])))
        rows.append({
            "category": category,
            "label": label,
            "choices": [{"component": component, "issues": check_compatibility([*other_components, component])} for component in choices],
            "selected": next((pk for pk in selected_ids if component_map.get(pk) and component_map[pk].category == category), ""),
        })
    questionnaire = None
    from recommendations.views import _customer, _previous_response
    questionnaire = _previous_response(request, _customer(request))
    estimates = []
    if questionnaire and not issues:
        for setting in questionnaire.game_settings.select_related("game").order_by("game__name"):
            supported = GameSettingSupport.objects.filter(game=setting.game, resolution=setting.resolution, graphics_preset=setting.graphics_preset).exists()
            estimates.append({"setting": setting, "fps": predict_temporary_fps(items, setting) if supported else None})
    if request.method == "POST":
        request.session["current_build"] = session_build
        if request.POST.get("action") == "shortlist":
            return redirect("recommendations:shortlist_save")
        if request.POST.get("action") == "complete" and not issues and available > 0:
            import uuid
            completed = request.session.get("completed_builds", [])
            build_id = uuid.uuid4().hex
            completed.append({"id": build_id, "name": session_build["name"], "components": session_build["components"]})
            request.session["completed_builds"] = completed
            messages.success(request, "Build saved for comparison.")
            return redirect("catalogue:configure")
        return redirect("catalogue:configure")
    request.session["current_build"] = session_build
    return render(request, "catalogue/configure.html", {
        "name": session_build.get("name", "My custom PC"), "preset_id": session_build.get("preset_id"),
        "rows": rows, "items": items, "issues": issues, "price": price, "fee": fee,
        "available": available, "estimates": estimates, "complete": not issues,
        "completed_builds": request.session.get("completed_builds", []),
    })


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


def accessory_list(request):
    accessories = Accessory.objects.filter(active=True).order_by("category", "name")
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    if query:
        accessories = accessories.filter(Q(name__icontains=query) | Q(category__icontains=query))
    if category:
        accessories = accessories.filter(category=category)
    categories = Accessory.objects.filter(active=True).order_by().values_list("category", flat=True).distinct()
    return render(request, "catalogue/accessory_list.html", {
        "accessories": [{"item": item, "available": available_accessory_quantity(item)} for item in accessories],
        "categories": categories,
        "query": query,
        "selected_category": category,
    })


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


def _preset_rows(configurations):
    fee = current_assembly_fee()
    presets = []
    for configuration in configurations:
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
    return presets, fee


def preset_manage_list(request):
    presets, fee = _preset_rows(
        Configuration.objects.prefetch_related("items__component").order_by("name")
    )
    return render(
        request,
        "catalogue/preset_manage_list.html",
        {
            "presets": presets,
            "assembly_fee": fee.fee_amount if fee else None,
            "has_fee": fee is not None,
        },
    )


def preset_list(request):
    query = Configuration.objects.filter(
        status=Configuration.Status.PUBLISHED
    ).prefetch_related("items__component").order_by("name")
    presets, fee = _preset_rows(query)
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()
    tier = request.GET.get("tier", "").strip()
    availability = request.GET.get("availability", "all")
    filter_error = ""
    try:
        minimum = Decimal(min_price) if min_price else None
        maximum = Decimal(max_price) if max_price else None
        if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
            raise InvalidOperation
    except InvalidOperation:
        minimum = maximum = None
        filter_error = "Enter valid non-negative price limits."
    tiers = sorted({
        item.component.ml_tier
        for row in presets
        for item in row["configuration"].items.all()
        if item.component.ml_tier
    })
    if tier:
        presets = [row for row in presets if any(
            item.component.ml_tier == tier for item in row["configuration"].items.all()
        )]
    if availability == "in_stock":
        presets = [row for row in presets if row["available_quantity"] > 0]
    elif availability == "out_of_stock":
        presets = [row for row in presets if row["available_quantity"] == 0]
    if minimum is not None:
        presets = [row for row in presets if row["price"] is not None and row["price"] >= minimum]
    if maximum is not None:
        presets = [row for row in presets if row["price"] is not None and row["price"] <= maximum]
    return render(
        request,
        "catalogue/preset_list.html",
        {
            "presets": presets,
            "assembly_fee": fee.fee_amount if fee else None,
            "has_fee": fee is not None,
            "min_price": min_price,
            "max_price": max_price,
            "tier": tier,
            "tiers": tiers,
            "availability": availability,
            "filter_error": filter_error,
        },
    )


def preset_detail(request, pk):
    from support.models import Review
    configuration = get_object_or_404(
        Configuration.objects.prefetch_related("items__component"),
        pk=pk,
        status=Configuration.Status.PUBLISHED,
    )
    presets, fee = _preset_rows([configuration])
    customer = Customer.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    questionnaire = None
    game_needs = []
    if customer:
        from recommendations.models import GameSettingSupport

        questionnaire = customer.questionnaires.order_by("-created_at", "-pk").first()
    else:
        from recommendations.models import GameSettingSupport, QuestionnaireResponse

    if not questionnaire and request.session.get("questionnaire_response_id"):
        from recommendations.models import QuestionnaireResponse

        saved_response = QuestionnaireResponse.objects.filter(
            pk=request.session["questionnaire_response_id"]
        ).first()
        if saved_response and (saved_response.customer_id is None or saved_response.customer_id == getattr(customer, "pk", None)):
            questionnaire = saved_response
    if questionnaire:
        from recommendations.predictor import predict_temporary_fps
        for setting in questionnaire.game_settings.select_related("game").order_by("game__name"):
            supported = GameSettingSupport.objects.filter(
                game=setting.game,
                resolution=setting.resolution,
                graphics_preset=setting.graphics_preset,
            ).exists()
            estimate = predict_temporary_fps(list(configuration.items.all()), setting) if supported else None
            game_needs.append({"setting": setting, "supported": supported, "estimate": estimate})
    return render(
        request,
        "catalogue/preset_detail.html",
        {
            "row": presets[0],
            "assembly_fee": fee.fee_amount if fee else None,
            "has_fee": fee is not None,
            "questionnaire": questionnaire,
            "game_needs": game_needs,
            "reviews": Review.objects.filter(
                Q(purchased_pc__order_line__configuration=configuration)
                | Q(purchased_pc__order_line__custom_snapshot__source_preset_id=configuration.pk)
            ).select_related("purchased_pc__order_line").order_by("-created_at", "-pk"),
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
