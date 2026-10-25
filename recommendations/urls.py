from django.urls import path
from . import views

app_name = "recommendations"

urlpatterns = [
    path("questionnaire", views.questionnaire, name="questionnaire"),
    path("recommendations", views.recommendations, name="recommendations"),
    path("games", views.placeholder, name="game_list"),
]
