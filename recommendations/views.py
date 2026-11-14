from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import Customer
from catalogue.drafts import build_revision
from catalogue.compatibility import check_configuration_components, check_preset_items
from catalogue.pricing import available_preset_quantity, calculate_pc_price, current_assembly_fee
from .forms import QuestionnaireForm, ShortlistForm
from catalogue.models import Component, Configuration, ConfigurationItem, specification_fields
from .models import GameSettingSupport, QuestionnaireGameSetting, QuestionnaireResponse, ShortlistItem
from .predictor import predict_fps
from .preferences import previous_response


@login_required
def shortlist(request):
    customer = get_object_or_404(Customer, user=request.user)
    builds = customer.shortlist.order_by("-created_at", "-pk")
    return render(request, "recommendations/shortlist.html", {"builds": builds})


@login_required
@require_http_methods(["GET", "POST"])
def shortlist_save(request):
    customer = get_object_or_404(Customer, user=request.user)
    build = request.session.get("current_build", {})
    if not build.get("components"):
        messages.error(request, "Choose at least one component before saving a build.")
        return redirect("catalogue:configure")
    form = ShortlistForm(request.POST if request.method == "POST" else None,
                         initial={"name": build.get("name", "My PC")})
    revision = build_revision(build)
    if request.method == "POST" and request.POST.get("build_revision") != revision:
        messages.error(request, "Your current build changed. Review the selections before saving.")
        return redirect("catalogue:configure")
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.customer = customer
        saved.configuration = Configuration.objects.filter(pk=build.get("preset_id")).first()
        # Store selections, not a live link to the preset's current component list.
        saved.custom_snapshot = {"components": build["components"]}
        saved.save()
        messages.success(request, "Build saved to your shortlist.")
        return redirect("recommendations:shortlist")
    return render(request, "recommendations/shortlist_form.html", {"form": form, "build_revision": revision})


def _shortlist_build(saved):
    components = saved.custom_snapshot.get("components")
    if components is None:
        # Older records may contain only a preset link.
        components = [{"component_id": item.component_id, "quantity": item.quantity}
                      for item in saved.configuration.items.all()] if saved.configuration_id else []
    return {"name": saved.name, "preset_id": saved.configuration_id, "components": components}


@login_required
@require_POST
def shortlist_reopen(request, pk):
    saved = get_object_or_404(ShortlistItem.objects.select_related("configuration"), pk=pk, customer__user=request.user)
    request.session["current_build"] = _shortlist_build(saved)
    messages.info(request, "Saved build reopened. Prices, stock and compatibility have been checked against the current catalogue.")
    return redirect("catalogue:configure")


@login_required
@require_POST
def shortlist_remove(request, pk):
    saved = get_object_or_404(ShortlistItem, pk=pk, customer__user=request.user)
    saved.delete()
    request.session["comparison_items"] = [item for item in request.session.get("comparison_items", [])
        if not (item.get("kind") == "shortlist" and str(item.get("id")) == str(pk))]
    messages.success(request, "Build removed from your shortlist.")
    return redirect("recommendations:shortlist")


def _comparison_selections(request):
    """Discard deleted or inaccessible selections before applying the three-build limit."""
    valid = []
    for item in request.session.get("comparison_items", []):
        kind, item_id = item.get("kind"), item.get("id")
        if kind == "preset":
            exists = Configuration.objects.filter(pk=item_id, status=Configuration.Status.PUBLISHED).exists()
        elif kind == "shortlist" and request.user.is_authenticated:
            exists = ShortlistItem.objects.filter(pk=item_id, customer__user=request.user).exists()
        elif kind == "custom":
            exists = any(str(build.get("id")) == str(item_id) for build in request.session.get("completed_builds", []))
        else:
            exists = False
        if exists and item not in valid and len(valid) < 3:
            valid.append(item)
    request.session["comparison_items"] = valid
    return valid


@require_http_methods(["GET", "POST"])
def questionnaire(request):
    customer = Customer.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    submitted = request.POST if request.method == "POST" else None
    previous = previous_response(request, customer) if submitted is None else None
    form = QuestionnaireForm(submitted, initial_response=previous)
    reviewing = submitted is not None and submitted.get("action") == "settings"
    if submitted is not None:
        missing = {
            name: field.initial for name, field in form.fields.items()
            if name.startswith("game_") and name not in submitted
        }
        if missing:
            reviewing = True
            data = submitted.copy()
            data.update(missing)
            form = QuestionnaireForm(data)
    if submitted is not None and not reviewing and form.is_valid():
        with transaction.atomic():
            response = QuestionnaireResponse.objects.create(
                customer=customer,
                perf_level="balanced",
                max_spend=form.cleaned_data["max_spend"],
            )
            for game in form.cleaned_data["games"]:
                prefix = f"game_{game.pk}"
                QuestionnaireGameSetting.objects.create(
                    response=response,
                    game=game,
                    fps_goal=form.cleaned_data[f"{prefix}_fps_goal"],
                    resolution=form.cleaned_data[f"{prefix}_resolution"],
                    graphics_preset=form.cleaned_data[f"{prefix}_graphics_preset"],
                )
        request.session["questionnaire_response_id"] = response.pk
        request.session.pop("questionnaire_draft", None)
        messages.success(request, "Your gaming needs have been saved.")
        return redirect("recommendations:recommendations")
    return render(
        request,
        "recommendations/questionnaire.html",
        {"form": form, "game_settings": form.game_settings},
    )


def recommendations(request):
    questionnaire = previous_response(request)
    if not questionnaire or not questionnaire.game_settings.exists():
        return render(request, "recommendations/results.html", {"questionnaire": questionnaire, "eligible": False})
    settings = list(questionnaire.game_settings.select_related("game").order_by("game__name"))
    fee = current_assembly_fee()
    if fee is None:
        return render(request, "recommendations/results.html", {"questionnaire": questionnaire, "eligible": False, "no_fee": True})

    eligible = []
    configurations = Configuration.objects.filter(status=Configuration.Status.PUBLISHED).prefetch_related(
        "items__component"
    ).order_by("name")
    for configuration in configurations:
        items = list(configuration.items.all())
        if check_preset_items(items):
            continue
        available = available_preset_quantity(items)
        if available < 1:
            continue
        estimates = []
        for setting in settings:
            supported = GameSettingSupport.objects.filter(
                game=setting.game,
                resolution=setting.resolution,
                graphics_preset=setting.graphics_preset,
            ).exists()
            fps = predict_fps(items, setting) if supported else None
            if fps is None:
                break
            estimates.append({"setting": setting, "fps": fps, "shortfall": max(0, setting.fps_goal - fps)})
        if len(estimates) != len(settings):
            continue
        price = calculate_pc_price(items, fee.fee_amount)
        performance = sum(estimate["fps"] for estimate in estimates)
        eligible.append({
            "configuration": configuration,
            "price": price,
            "available": available,
            "estimates": estimates,
            "performance": performance,
            "value": Decimal(performance) / price,
            "meets_goals": all(estimate["shortfall"] == 0 for estimate in estimates),
        })

    affordable = [row for row in eligible if row["price"] <= questionnaire.max_spend]
    affordable_qualifying = [row for row in affordable if row["meets_goals"]]
    profiles = []
    alternatives = []
    over_budget = None
    if affordable_qualifying:
        choices = [
            ("Lowest cost meeting all goals", min(affordable_qualifying, key=lambda row: row["price"]), "Lowest-priced available build that meets every target."),
            ("Best performance within budget", min(affordable, key=lambda row: (-row["performance"], row["price"])), "Highest combined estimated FPS within your budget."),
            ("Best value", min(affordable, key=lambda row: (-row["value"], row["price"])), "Highest combined estimated FPS per dollar within your budget."),
        ]
        for label, row, reason in choices:
            existing = next((profile for profile in profiles if profile["row"] is row), None)
            if existing:
                existing["labels"].append(label)
                existing["reasons"].append(reason)
            else:
                profiles.append({"row": row, "labels": [label], "reasons": [reason]})
    else:
        alternatives = sorted(
            affordable,
            key=lambda row: (sum(item["shortfall"] for item in row["estimates"]), row["price"]),
        )[:3]
        over_budget = min(
            (row for row in eligible if row["meets_goals"] and row["price"] > questionnaire.max_spend),
            key=lambda row: row["price"],
            default=None,
        )
    return render(
        request,
        "recommendations/results.html",
        {
            "questionnaire": questionnaire,
            "eligible": bool(eligible),
            "profiles": profiles,
            "alternatives": alternatives,
            "over_budget": over_budget,
            "extra_cost": over_budget["price"] - questionnaire.max_spend if over_budget else None,
            "synthetic_estimates": True,
        },
    )


@require_POST
def compare_add(request):
    kind = request.POST.get("kind", "preset")
    raw_id = request.POST.get("item_id", "")
    try:
        item_id = raw_id if kind == "custom" else int(raw_id)
        if kind != "custom" and not 0 < item_id <= 9223372036854775807:
            raise ValueError
    except ValueError:
        messages.error(request, "Choose a valid build to compare.")
        return redirect("recommendations:compare")
    if kind == "preset":
        exists = Configuration.objects.filter(pk=item_id, status=Configuration.Status.PUBLISHED).exists()
    elif kind == "shortlist" and request.user.is_authenticated:
        exists = ShortlistItem.objects.filter(pk=item_id, customer__user=request.user).exists()
    elif kind == "custom":
        exists = any(str(item.get("id")) == str(item_id) for item in request.session.get("completed_builds", []))
    else:
        exists = False
    if not exists:
        messages.error(request, "That build is unavailable to compare.")
    else:
        selected = _comparison_selections(request)
        item = {"kind": kind, "id": item_id}
        if item not in selected:
            if len(selected) >= 3:
                messages.error(request, "Compare up to three builds at a time.")
            else:
                selected.append(item)
                request.session["comparison_items"] = selected
                messages.success(request, "Build added to comparison.")
    return redirect("recommendations:compare")


@require_POST
def compare_remove(request):
    key = {"kind": request.POST.get("kind"), "id": request.POST.get("item_id")}
    selected = request.session.get("comparison_items", [])
    request.session["comparison_items"] = [
        item for item in selected if not (item.get("kind") == key["kind"] and str(item.get("id")) == key["id"])
    ]
    return redirect("recommendations:compare")


def compare_builds(request):
    selected = _comparison_selections(request)
    questionnaire = previous_response(request)
    settings = list(questionnaire.game_settings.select_related("game").order_by("game__name")) if questionnaire else []
    builds = []
    fee = current_assembly_fee()
    for selection in selected:
        kind, item_id = selection.get("kind"), selection.get("id")
        if kind == "preset":
            configuration = Configuration.objects.filter(
                pk=item_id, status=Configuration.Status.PUBLISHED
            ).prefetch_related("items__component").first()
            if not configuration:
                continue
            items = list(configuration.items.all())
            name = configuration.name
        elif kind == "shortlist" and request.user.is_authenticated:
            saved = ShortlistItem.objects.filter(
                pk=item_id, customer__user=request.user
            ).select_related("configuration").first()
            if not saved:
                continue
            name = saved.name
            items = _snapshot_items(_shortlist_build(saved))
        elif kind == "custom":
            saved = next((item for item in request.session.get("completed_builds", []) if str(item.get("id")) == str(item_id)), None)
            if not saved:
                continue
            name = saved.get("name", "Custom build")
            items = _snapshot_items(saved)
        else:
            continue
        issues = check_preset_items(items) if kind == "preset" else check_configuration_components(item.component for item in items)
        if kind in ("shortlist", "custom"):
            snapshot = _shortlist_build(saved) if kind == "shortlist" else saved
            missing = {part["component_id"] for part in snapshot.get("components", [])} - {item.component_id for item in items}
            if missing:
                issues.append("Some saved components are no longer in the catalogue. Reopen the build to choose replacements.")
        price = None
        if fee and not issues:
            price = calculate_pc_price(items, fee.fee_amount)
        available = available_preset_quantity(items) if not issues else 0
        by_category = {}
        for item in items:
            label = item.component.get_category_display()
            component = item.component
            specs = [
                f"Socket {component.socket}" if component.socket else "",
                f"Memory {component.memory_type}" if component.memory_type else "",
                f"Form factor {component.form_factor}" if component.form_factor else "",
                f"Tier {component.ml_tier}" if component.ml_tier else "",
            ]
            specs.extend(f"{key}: {value}" for key, value in specification_fields(component.specs).items())
            specs = [value for value in specs if value]
            part = f"{component.brand} {component.model} × {item.quantity}"
            if specs:
                part += f" ({'; '.join(specs)})"
            by_category[label] = f"{by_category[label]}; {part}" if label in by_category else part
        estimates = {}
        if not issues:
            for setting in settings:
                supported = GameSettingSupport.objects.filter(
                    game=setting.game, resolution=setting.resolution,
                    graphics_preset=setting.graphics_preset,
                ).exists()
                estimates[setting.pk] = predict_fps(items, setting) if supported else None
        builds.append({
            "kind": kind,
            "id": item_id,
            "name": name,
            "price": price,
            "available": available,
            "issues": issues,
            "by_category": by_category,
            "estimates": estimates,
        })
    category_rows = []
    categories = ("CPU", "Motherboard", "Memory", "Graphics card", "Storage", "Power supply", "Case", "CPU cooler")
    for category in categories:
        category_rows.append({"label": category, "values": [build["by_category"].get(category, "—") for build in builds]})
    overview_rows = [
        {"label": "PC price", "values": [f"${build['price']}" if build["price"] is not None else "Unavailable" for build in builds]},
        {"label": "Available now", "values": [str(build["available"]) for build in builds]},
    ]
    game_rows = []
    for setting in settings:
        estimates = [build["estimates"].get(setting.pk) for build in builds]
        game_rows.append({
            "setting": setting,
            "values": [
                {"fps": fps, "shortfall": max(0, setting.fps_goal - fps)} if fps is not None else None
                for fps in estimates
            ],
        })
    return render(
        request,
        "recommendations/compare.html",
        {
            "builds": builds,
            "overview_rows": overview_rows,
            "category_rows": category_rows,
            "game_rows": game_rows,
            "questionnaire": questionnaire,
        },
    )


def _snapshot_items(snapshot):
    component_rows = snapshot.get("components", [])
    components = Component.objects.in_bulk(
        row.get("component_id") for row in component_rows if row.get("component_id")
    )
    return [
        ConfigurationItem(component=components[row["component_id"]], quantity=row.get("quantity", 1))
        for row in component_rows
        if row.get("component_id") in components and row.get("quantity", 0) > 0
    ]
