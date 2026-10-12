from django.urls import path
from accounts.views import staff_required
from . import views

app_name = "catalogue"

urlpatterns = [
    path("", views.placeholder, name="home"),
    path("components", views.placeholder, name="components"),
    path("staff/components", staff_required(views.component_list), name="component_list"),
    path("staff/components/new", staff_required(views.component_create), name="component_create"),
    path("staff/components/<int:pk>/edit", staff_required(views.component_update), name="component_update"),
    path("presets", views.placeholder, name="preset_list"),
    path("staff/presets/new", staff_required(views.placeholder), name="preset_create"),
    path("presets/<int:pk>", views.placeholder, name="preset_detail"),
    path("configure", views.placeholder, name="configure"),
    path("accessories", views.placeholder, name="accessory_list"),
]
