from django.urls import path
from . import views

app_name = "recommendations"

urlpatterns = [
    path("shortlist", views.shortlist, name="shortlist"),
    path("shortlist/save", views.shortlist_save, name="shortlist_save"),
    path("shortlist/<int:pk>/reopen", views.shortlist_reopen, name="shortlist_reopen"),
    path("shortlist/<int:pk>/remove", views.shortlist_remove, name="shortlist_remove"),
    path("questionnaire", views.questionnaire, name="questionnaire"),
    path("recommendations", views.recommendations, name="recommendations"),
    path("compare", views.compare_builds, name="compare"),
    path("compare/add", views.compare_add, name="compare_add"),
    path("compare/remove", views.compare_remove, name="compare_remove"),
    path("games", views.placeholder, name="game_list"),
]
