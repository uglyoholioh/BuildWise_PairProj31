from django.urls import path
from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.inventory_dashboard, name="inventory_dashboard"),
    path("adjustments/new", views.adjustment_create, name="adjustment_create"),
    path("inspections/<int:pk>", views.inspect_cancelled_stock, name="inspect_cancelled_stock"),
]
