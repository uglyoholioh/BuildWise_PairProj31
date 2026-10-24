from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import Customer
from .forms import QuestionnaireForm
from .models import QuestionnaireGameSetting, QuestionnaireResponse


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
