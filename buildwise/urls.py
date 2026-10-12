from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("catalogue.urls")),
    path("accounts/", include("accounts.urls")),
    path("panel/", include("inventory.urls")),
    path("", include("orders.urls")),
    path("", include("recommendations.urls")),
    path("", include("support.urls")),
]
