from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Customer
from catalogue.models import AssemblyFeeSetting, Component, Configuration
from catalogue.compatibility import check_configuration_components
from catalogue.pricing import available_preset_quantity, calculate_pc_price
from .forms import QuestionnaireForm
from catalogue.models import ConfigurationItem
from inventory.availability import available_component_quantity
from .models import GameSettingSupport, QuestionnaireGameSetting, QuestionnaireResponse, ShortlistItem
from .predictor import predict_temporary_fps


def placeholder(request, *args, **kwargs):
    return render(request, "recommendations/placeholder.html")


def _customer(request):
    return Customer.objects.filter(user=request.user).first() if request.user.is_authenticated else None


def _previous_response(request, customer):
    if customer:
        response = customer.questionnaires.order_by("-created_at", "-pk").first()
        if response:
            return response
    response_id = request.session.get("questionnaire_response_id")
    if response_id:
        response = QuestionnaireResponse.objects.filter(pk=response_id).first()
        if response and (response.customer_id is None or response.customer_id == getattr(customer, "pk", None)):
            return response
    return None


def questionnaire(request):
    customer = _customer(request)
    previous = _previous_response(request, customer)
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
        messages.success(request, "Your gaming needs have been saved.")
        return redirect("recommendations:recommendations")
    return render(
        request,
        "recommendations/questionnaire.html",
        {"form": form, "game_settings": form.game_settings},
    )


def recommendations(request):
    questionnaire = _previous_response(request, _customer(request))
    if not questionnaire or not questionnaire.game_settings.exists():
        return render(request, "recommendations/results.html", {"questionnaire": questionnaire, "eligible": False})
    settings = list(questionnaire.game_settings.select_related("game").order_by("game__name"))
    fee = AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()
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
            fps = predict_temporary_fps(items, setting) if supported else None
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
            "temporary_estimates": True,
        },
    )


@require_POST
def compare_add(request):
    kind = request.POST.get("kind", "preset")
    raw_id = request.POST.get("item_id", "")
    try:
        item_id = raw_id if kind == "custom" else int(raw_id)
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
        selected = request.session.get("comparison_items", [])
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
    selected = request.session.get("comparison_items", [])[:3]
    builds = []
    fee = AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()
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
            if saved.configuration_id:
                configuration = saved.configuration
                items = list(configuration.items.select_related("component"))
            else:
                items = _snapshot_items(saved.custom_snapshot)
        elif kind == "custom":
            saved = next((item for item in request.session.get("completed_builds", []) if str(item.get("id")) == str(item_id)), None)
            if not saved:
                continue
            name = saved.get("name", "Custom build")
            items = _snapshot_items(saved)
        else:
            continue
        issues = check_configuration_components(item.component for item in items)
        price = None
        if fee and not issues:
            price = calculate_pc_price(items, fee.fee_amount)
        available = min(
            (available_component_quantity(item.component) // item.quantity for item in items),
            default=0,
        ) if items else 0
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
            specs.extend(f"{key}: {value}" for key, value in component.specs.items())
            specs = [value for value in specs if value]
            part = f"{component.brand} {component.model} × {item.quantity}"
            if specs:
                part += f" ({'; '.join(specs)})"
            by_category[label] = f"{by_category[label]}; {part}" if label in by_category else part
        questionnaire = _previous_response(request, _customer(request))
        estimates = []
        if questionnaire:
            for setting in questionnaire.game_settings.select_related("game").order_by("game__name"):
                supported = GameSettingSupport.objects.filter(
                    game=setting.game, resolution=setting.resolution,
                    graphics_preset=setting.graphics_preset,
                ).exists()
                fps = predict_temporary_fps(items, setting) if supported and not issues else None
                estimates.append({"setting": setting, "fps": fps})
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
    questionnaire = _previous_response(request, _customer(request))
    game_rows = []
    if questionnaire:
        for setting in questionnaire.game_settings.select_related("game").order_by("game__name"):
            game_rows.append({
                "label": f"{setting.game.name} · {setting.resolution} · {setting.graphics_preset} · target {setting.fps_goal} FPS (estimate)",
                "values": [
                    next((str(row["fps"]) + " FPS" if row["fps"] is not None else "Unavailable" for row in build["estimates"] if row["setting"].pk == setting.pk), "Unavailable")
                    for build in builds
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
