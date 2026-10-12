from django.urls import path
from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.placeholder, name="inventory_dashboard"),
    path("adjustments/new", views.placeholder, name="adjustment_create"),
]
