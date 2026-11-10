from django.urls import include, path
from accounts import views as account_views

urlpatterns = [
    path("staff/login", account_views.StaffLoginView.as_view(), name="staff_login"),
    path("staff/employees", account_views.employee_list, name="employee_list"),
    path("staff/employees/new", account_views.employee_create, name="employee_create"),
    path("staff/employees/<int:pk>/edit", account_views.employee_update, name="employee_update"),
    path("", include("catalogue.urls")),
    path("accounts/", include("accounts.urls")),
    path("staff/", include("inventory.urls")),
    path("", include("orders.urls")),
    path("", include("recommendations.urls")),
    path("", include("support.urls")),
]
