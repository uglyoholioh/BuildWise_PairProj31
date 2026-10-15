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
    path("staff/compatibility", staff_required(views.compatibility_rules), name="compatibility_rules"),
    path("staff/compatibility/<int:pk>/delete", staff_required(views.compatibility_rule_delete), name="compatibility_rule_delete"),
    path("presets", views.placeholder, name="preset_list"),
    path("staff/presets", staff_required(views.preset_list), name="preset_manage_list"),
    path("staff/presets/new", staff_required(views.preset_create), name="preset_create"),
    path("staff/presets/<int:pk>/edit", staff_required(views.preset_update), name="preset_update"),
    path("staff/assembly-fee", staff_required(views.assembly_fee), name="assembly_fee"),
    path("presets/<int:pk>", views.placeholder, name="preset_detail"),
    path("configure", views.placeholder, name="configure"),
    path("accessories", views.placeholder, name="accessory_list"),
]
