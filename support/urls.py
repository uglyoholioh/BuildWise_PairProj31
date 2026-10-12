from django.urls import path
from accounts.views import staff_required
from . import views

app_name = "support"

urlpatterns = [
    path("support", views.placeholder, name="request_list"),
    path("support/new", views.placeholder, name="request_create"),
    path("support/<int:pk>", views.placeholder, name="request_detail"),
    path("staff/support", staff_required(views.placeholder), name="staff_request_list"),
]
