from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("staff/customers", views.customer_list, name="customer_list"),
    path("staff/customers/<int:pk>", views.customer_detail, name="customer_detail"),
    path("profile", views.placeholder, name="profile"),
    path("register", views.placeholder, name="register"),
    path("login", views.placeholder, name="login"),
    path("logout", views.logout_view, name="logout"),
]
