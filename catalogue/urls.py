from django.urls import path
from . import views

app_name = "catalogue"

urlpatterns = [
    path("", views.preset_list, name="home"),
    path("staff/components", views.component_list, name="component_list"),
    path("staff/components/new", views.component_create, name="component_create"),
    path("staff/components/<int:pk>/edit", views.component_update, name="component_update"),
    path("staff/accessories", views.accessory_manage_list, name="accessory_manage_list"),
    path("staff/accessories/new", views.accessory_create, name="accessory_create"),
    path("staff/accessories/<int:pk>/edit", views.accessory_update, name="accessory_update"),
    path("staff/compatibility", views.compatibility_rules, name="compatibility_rules"),
    path("staff/compatibility/<int:pk>/delete", views.compatibility_rule_delete, name="compatibility_rule_delete"),
    path("presets", views.preset_list, name="preset_list"),
    path("staff/presets", views.preset_manage_list, name="preset_manage_list"),
    path("staff/presets/new", views.preset_create, name="preset_create"),
    path("staff/presets/<int:pk>/edit", views.preset_update, name="preset_update"),
    path("staff/assembly-fee", views.assembly_fee, name="assembly_fee"),
    path("presets/<int:pk>", views.preset_detail, name="preset_detail"),
    path("configure", views.configure, name="configure"),
    path("configure/new", views.start_new_build, name="start_new_build"),
    path("accessories", views.accessory_list, name="accessory_list"),
]
