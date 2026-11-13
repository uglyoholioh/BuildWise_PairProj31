from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import Customer
from catalogue.compatibility import check_configuration_components
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
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.customer = customer
        saved.configuration = Configuration.objects.filter(pk=build.get("preset_id")).first()
        # Store selections, not a live link to the preset's current component list.
        saved.custom_snapshot = {"components": build["components"]}
        saved.save()
        messages.success(request, "Build saved to your shortlist.")
        return redirect("recommendations:shortlist")
    return render(request, "recommendations/shortlist_form.html", {"form": form})


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


def questionnaire(request):
    customer = Customer.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    previous = previous_response(request, customer)
    initial = None
    if previous and request.method == "GET":
        initial = {
            "games": list(previous.game_settings.values_list("game_id", flat=True)),
            "perf_level": previous.perf_level,
            "max_spend": previous.max_spend,
        }
    form = QuestionnaireForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
        initial_response=previous,
    )
    if request.method == "POST":
        selection = QuestionnaireForm(request.POST, include_settings=False)
        if not selection.is_valid():
            form = selection
        else:
            draft = request.session.get("questionnaire_draft", {})
            # Keep edits to removed games so re-adding a game restores its settings.
            for game_id in GameSettingSupport.objects.values_list("game_id", flat=True).distinct():
                for suffix in ("fps_goal", "resolution", "graphics_preset"):
                    key = f"game_{game_id}_{suffix}"
                    if key in request.POST:
                        draft[key] = request.POST[key]
            request.session["questionnaire_draft"] = draft
            needs_settings = any(name not in request.POST for name in form.fields if name.startswith("game_"))
            if request.POST.get("action") == "settings" or needs_settings:
                initial = dict(draft)
                initial.update({"games": list(selection.cleaned_data["games"].values_list("pk", flat=True)),
                                "perf_level": selection.cleaned_data["perf_level"],
                                "max_spend": selection.cleaned_data["max_spend"]})
                form = QuestionnaireForm(initial=initial, initial_response=previous)
                return render(request, "recommendations/questionnaire.html", {
                    "form": form, "game_settings": form.game_settings,
                })
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            response = QuestionnaireResponse.objects.create(
                customer=customer,
                perf_level=form.cleaned_data["perf_level"],
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
    unsupported_count = 0
    configurations = Configuration.objects.filter(status=Configuration.Status.PUBLISHED).prefetch_related(
        "items__component"
    ).order_by("name")
    for configuration in configurations:
        items = list(configuration.items.all())
        if check_configuration_components(item.component for item in items):
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
            unsupported_count += 1
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
    qualifying = [row for row in eligible if row["meets_goals"]]
    profiles = []
    alternatives = []
    over_budget = None
    if any(row["meets_goals"] for row in affordable):
        choices = [
            ("Lowest cost meeting all goals", min((row for row in affordable if row["meets_goals"]), key=lambda row: row["price"]), "Lowest-priced available build that meets every target."),
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
    elif affordable:
        alternatives = sorted(
            affordable,
            key=lambda row: (sum(item["shortfall"] for item in row["estimates"]), row["price"]),
        )[:3]
        over_budget = min(
            (row for row in qualifying if row["price"] > questionnaire.max_spend),
            key=lambda row: row["price"],
            default=None,
        )
    else:
        over_budget = min(
            (row for row in qualifying if row["price"] > questionnaire.max_spend),
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
            "unsupported_count": unsupported_count,
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
        issues = check_configuration_components(item.component for item in items)
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
