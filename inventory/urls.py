from django.urls import path
from accounts.views import staff_required
from . import views

app_name = "inventory"

urlpatterns = [
    path("", staff_required(views.inventory_dashboard), name="inventory_dashboard"),
    path("adjustments/new", staff_required(views.adjustment_create), name="adjustment_create"),
    path("inspections/<int:pk>", staff_required(views.inspect_cancelled_stock), name="inspect_cancelled_stock"),
]
