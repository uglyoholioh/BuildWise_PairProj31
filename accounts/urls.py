from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("profile", views.placeholder, name="profile"),
    path("register", views.placeholder, name="register"),
    path("login", views.placeholder, name="login"),
    path("logout", views.placeholder, name="logout"),
    path("panel/login", views.placeholder, name="staff_login"),
    path("panel/employees", views.placeholder, name="employee_list"),
]
