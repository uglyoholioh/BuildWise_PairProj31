from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import StaffAction
from accounts.views import active_manager, staff_required
from inventory.availability import available_accessory_quantity
from support.models import Review
from .compatibility import check_compatibility, check_configuration_components, check_preset_items, revalidate_configurations
from .forms import (
    AccessoryForm,
    AssemblyFeeForm,
    BuildForm,
    ComponentConflictForm,
    ComponentForm,
    ConfigurationForm,
    ConfigurationItemFormSet,
    PresetFilterForm,
)
from .models import Accessory, AssemblyFeeSetting, Component, ComponentConflict, Configuration, ConfigurationItem
from .drafts import build_revision
from .pricing import available_preset_quantity, calculate_pc_price, current_assembly_fee
from recommendations.accessories import accessory_suggestions
from recommendations.models import GameSettingSupport
from recommendations.predictor import predict_fps
from recommendations.preferences import previous_response


@require_POST
def start_new_build(request):
    request.session.pop("current_build", None)
    return redirect("catalogue:configure")


@require_http_methods(["GET", "POST"])
def configure(request):
    session_build = request.session.get("current_build", {})
    action = request.POST.get("action")
    if action in ("complete", "shortlist") and request.POST.get("build_revision") != build_revision(session_build):
        messages.error(request, "Your current build changed. Review the selections before saving.")
        return redirect("catalogue:configure")
    preset_id = request.GET.get("preset") if request.method == "GET" else None
    if preset_id:
        try:
            preset_id = int(preset_id)
        except ValueError:
            raise Http404("Invalid preset.") from None
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
    form = BuildForm(request.POST if request.method == "POST" else None)
    valid_submission = form.is_bound and form.is_valid()
    if valid_submission:
        previous_quantities = selected_ids
        previous_components = Component.objects.in_bulk(previous_quantities)
        selected_ids = {}
        for category in Component.Category.values:
            component = form.cleaned_data[f"component_{category}"]
            if not component:
                continue
            category_parts = [
                pk for pk in previous_quantities
                if pk in previous_components and previous_components[pk].category == category
            ]
            if category_parts and component.pk == category_parts[0]:
                selected_ids.update({pk: previous_quantities[pk] for pk in category_parts})
            else:
                selected_ids[component.pk] = previous_quantities.get(component.pk, 1)
        session_build = {
            "name": form.cleaned_data["name"] or "My custom PC",
            "preset_id": session_build.get("preset_id"),
            "components": [{"component_id": pk, "quantity": quantity} for pk, quantity in selected_ids.items()],
        }
        request.session["current_build"] = session_build
        if action == "shortlist":
            return redirect("recommendations:shortlist_save")
        if action != "complete":
            return redirect("catalogue:configure")
    elif preset_id:
        request.session["current_build"] = session_build
    components = Component.objects.filter(pk__in=selected_ids)
    component_map = {component.pk: component for component in components}
    items = [ConfigurationItem(component=component_map[pk], quantity=quantity) for pk, quantity in selected_ids.items() if pk in component_map]
    issues = check_configuration_components(item.component for item in items)
    for component_id in selected_ids.keys() - component_map.keys():
        issues.append(f"Saved component #{component_id} is no longer in the catalogue. Choose a replacement or update the build to remove it.")
    fee = current_assembly_fee()
    price = calculate_pc_price(items, fee.fee_amount) if fee and not issues else None
    available = available_preset_quantity(items) if items and not issues else 0
    if valid_submission:
        if not issues and available > 0 and fee:
            completed = request.session.get("completed_builds", [])
            completed.append({"id": uuid4().hex, "name": session_build["name"], "components": session_build["components"]})
            request.session["completed_builds"] = completed
            messages.success(request, "Build saved for comparison.")
        else:
            messages.error(request, "Complete an available, compatible build before saving it for comparison.")
        return redirect("catalogue:configure")
    rows = []
    selected_components = (
        [value for field, value in form.cleaned_data.items() if field.startswith("component_") and value]
        if form.errors else list(component_map.values())
    )
    for category, label in Component.Category.choices:
        choices = Component.objects.filter(
            Q(active=True) | Q(pk__in=selected_ids), category=category,
        ).order_by("brand", "model")
        other_components = [component for component in selected_components if component.category != category]
        options = [
            {"component": component, "issues": check_compatibility([*other_components, component])}
            for component in choices
        ]
        options.sort(key=lambda option: bool(option["issues"]))
        field = form[f"component_{category}"]
        selected = field.value() if form.is_bound else next(
            (pk for pk in selected_ids if pk in component_map and component_map[pk].category == category), ""
        )
        rows.append({
            "category": category,
            "label": label,
            "choices": options,
            "selected": str(selected or ""),
            "errors": field.errors,
        })
    if form.errors:
        items, issues, price, available = [], [], None, 0
    questionnaire = previous_response(request)
    estimates = []
    if questionnaire and not issues and not form.errors:
        for setting in questionnaire.game_settings.select_related("game").order_by("game__name"):
            supported = GameSettingSupport.objects.filter(game=setting.game, resolution=setting.resolution, graphics_preset=setting.graphics_preset).exists()
            estimates.append({"setting": setting, "fps": predict_fps(items, setting) if supported else None})
    return render(request, "catalogue/configure.html", {
        "name": form["name"].value() if form.is_bound else session_build.get("name", "My custom PC"),
        "form": form, "preset_id": session_build.get("preset_id"), "build_revision": build_revision(session_build),
        "rows": rows, "items": items, "issues": issues, "price": price, "fee": fee,
        "available": available, "estimates": estimates, "complete": not issues and not form.errors and fee is not None,
        "completed_builds": request.session.get("completed_builds", []),
        "accessory_suggestions": accessory_suggestions(items) if items and not issues else [],
    })


@staff_required
def component_list(request):
    return render(
        request,
        "catalogue/component_list.html",
        {"components": Component.objects.order_by("category", "brand", "model")},
    )


@staff_required
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


@staff_required
@require_http_methods(["GET", "POST"])
def component_update(request, pk):
    component = get_object_or_404(Component, pk=pk)
    old_active = component.active
    form = ComponentForm(request.POST if request.method == "POST" else None, instance=component)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            component = form.save()
            moved_to_draft = revalidate_configurations([component])
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
        if moved_to_draft:
            messages.warning(request, f"Moved to Draft in Manage PC presets: {', '.join(moved_to_draft)}.")
        return redirect("catalogue:component_list")
    return render(
        request,
        "catalogue/component_form.html",
        {"form": form, "title": f"Edit {component.brand} {component.model}"},
    )


@staff_required
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


@staff_required
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


@staff_required
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


@staff_required
@require_http_methods(["GET", "POST"])
def compatibility_rules(request):
    form = ComponentConflictForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        with transaction.atomic():
            if form.is_valid():
                conflict = form.save()
                moved_to_draft = revalidate_configurations([conflict.component_a, conflict.component_b])
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
                if moved_to_draft:
                    messages.warning(request, f"Moved to Draft in Manage PC presets: {', '.join(moved_to_draft)}.")
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


@staff_required
@require_POST
def compatibility_rule_delete(request, pk):
    with transaction.atomic():
        conflict = get_object_or_404(
            ComponentConflict.objects.select_related(
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


def _preset_rows(configurations):
    fee = current_assembly_fee()
    presets = []
    for configuration in configurations:
        items = list(configuration.items.all())
        issues = check_preset_items(items)
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


@staff_required
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
    has_published_presets = query.exists()
    demo_presets = [] if has_published_presets else Configuration.objects.filter(
        status=Configuration.Status.DRAFT, name__startswith="[Demo]"
    ).prefetch_related("items__component").order_by("name")
    presets, fee = _preset_rows(query)
    tiers = sorted({
        item.component.ml_tier
        for row in presets
        for item in row["configuration"].items.all()
        if item.component.category == Component.Category.GPU and item.component.ml_tier
    })
    filter_form = PresetFilterForm(request.GET, tiers=tiers)
    filter_form.is_valid()
    minimum = filter_form.cleaned_data.get("min_price") if not filter_form.errors else None
    maximum = filter_form.cleaned_data.get("max_price") if not filter_form.errors else None
    tier = filter_form.cleaned_data.get("tier")
    availability = filter_form.cleaned_data.get("availability") or "all"
    if tier:
        presets = [row for row in presets if any(
            item.component.category == Component.Category.GPU and item.component.ml_tier == tier
            for item in row["configuration"].items.all()
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
            "demo_presets": demo_presets,
            "has_published_presets": has_published_presets,
            "assembly_fee": fee.fee_amount if fee else None,
            "has_fee": fee is not None,
            "min_price": filter_form["min_price"].value(),
            "max_price": filter_form["max_price"].value(),
            "tier": request.GET.get("tier", ""),
            "tiers": tiers,
            "availability": filter_form["availability"].value() or "all",
            "filter_form": filter_form,
            "has_build_draft": bool(request.session.get("current_build", {}).get("components")
                                    or request.session.get("current_build", {}).get("preset_id")),
        },
    )


def preset_detail(request, pk):
    configuration = get_object_or_404(
        Configuration.objects.prefetch_related("items__component"),
        pk=pk,
        status=Configuration.Status.PUBLISHED,
    )
    presets, fee = _preset_rows([configuration])
    questionnaire = previous_response(request)
    game_needs = []
    if questionnaire:
        for setting in questionnaire.game_settings.select_related("game").order_by("game__name"):
            supported = GameSettingSupport.objects.filter(
                game=setting.game,
                resolution=setting.resolution,
                graphics_preset=setting.graphics_preset,
            ).exists()
            estimate = predict_fps(list(configuration.items.all()), setting) if supported else None
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
            "accessory_suggestions": accessory_suggestions(configuration.items.all()),
            "reviews": Review.objects.filter(
                Q(purchased_pc__order_line__configuration=configuration)
                | Q(purchased_pc__order_line__custom_snapshot__source_preset_id=configuration.pk)
            ).select_related("purchased_pc__order_line").order_by("-created_at", "-pk"),
        },
    )


def _edit_preset(request, pk=None):
    creating = pk is None
    if request.method == "POST":
        with transaction.atomic():
            configuration = Configuration() if creating else get_object_or_404(Configuration, pk=pk)
            form = ConfigurationForm(request.POST, instance=configuration)
            formset = ConfigurationItemFormSet(request.POST, instance=configuration, prefix="items")
            form_valid = form.is_valid()
            items_valid = formset.is_valid()
            if form_valid and items_valid:
                publishing = form.cleaned_data["status"] == Configuration.Status.PUBLISHED
                if publishing and current_assembly_fee() is None:
                    form.add_error("status", "Set the shared assembly fee before publishing a preset.")
                if not form.errors:
                    try:
                        with transaction.atomic():
                            configuration = form.save()
                            formset.instance = configuration
                            formset.save()
                            if publishing:
                                for issue in check_preset_items(configuration.items.select_related("component")):
                                    form.add_error(None, issue)
                                if form.errors:
                                    transaction.set_rollback(True)
                    except IntegrityError:
                        form.add_error(None, "These component rows could not be saved. Reload the preset and try again.")
                if not form.errors:
                    StaffAction.objects.create(
                        employee=request.user.employee_profile,
                        action_type="configuration_created" if creating else "configuration_updated",
                        object_ref=str(configuration.pk),
                        detail=f"Saved preset {configuration.name} with status {configuration.status}.",
                    )
                    messages.success(request, "Preset saved.")
                    return redirect("catalogue:preset_manage_list")
    else:
        configuration = Configuration() if creating else get_object_or_404(Configuration, pk=pk)
        form = ConfigurationForm(instance=configuration)
        formset = ConfigurationItemFormSet(instance=configuration, prefix="items")
    return render(
        request,
        "catalogue/preset_form.html",
        {"form": form, "formset": formset, "title": "Add preset" if creating else f"Edit {configuration.name}"},
    )


@staff_required
@require_http_methods(["GET", "POST"])
def preset_create(request):
    return _edit_preset(request)


@staff_required
@require_http_methods(["GET", "POST"])
def preset_update(request, pk):
    return _edit_preset(request, pk)


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
