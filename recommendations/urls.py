from django.urls import path
from . import views

app_name = "recommendations"

urlpatterns = [
    path("questionnaire", views.questionnaire, name="questionnaire"),
    path("recommendations", views.recommendations, name="recommendations"),
    path("compare", views.compare_builds, name="compare"),
    path("compare/add", views.compare_add, name="compare_add"),
    path("compare/remove", views.compare_remove, name="compare_remove"),
    path("games", views.placeholder, name="game_list"),
]
