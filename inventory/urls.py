from django.urls import path
from accounts.views import staff_required
from . import views

app_name = "inventory"

urlpatterns = [
    path("", staff_required(views.placeholder), name="inventory_dashboard"),
    path("adjustments/new", staff_required(views.placeholder), name="adjustment_create"),
]
