from django.contrib import admin
from django.urls import include, path
from accounts import views as account_views

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("staff/login", account_views.StaffLoginView.as_view(), name="staff_login"),
    path("staff/employees", account_views.staff_required(account_views.placeholder), name="employee_list"),
    path("", include("catalogue.urls")),
    path("accounts/", include("accounts.urls")),
    path("staff/", include("inventory.urls")),
    path("", include("orders.urls")),
    path("", include("recommendations.urls")),
    path("", include("support.urls")),
]
