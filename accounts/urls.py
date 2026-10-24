from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("staff/customers", views.customer_list, name="customer_list"),
    path("staff/customers/<int:pk>", views.customer_detail, name="customer_detail"),
    path("profile", views.customer_profile, name="profile"),
    path("register", views.customer_register, name="register"),
    path("login", views.CustomerLoginView.as_view(), name="login"),
    path("logout", views.logout_view, name="logout"),
]
