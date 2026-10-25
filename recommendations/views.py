from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from accounts.models import Customer
from catalogue.compatibility import check_configuration_components
from catalogue.models import AssemblyFeeSetting, Configuration
from catalogue.pricing import available_preset_quantity, calculate_pc_price
from .forms import QuestionnaireForm
from .models import GameSettingSupport, QuestionnaireGameSetting, QuestionnaireResponse
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
