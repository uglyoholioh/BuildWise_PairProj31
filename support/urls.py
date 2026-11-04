from django.urls import path
from . import views

app_name = "support"

urlpatterns = [
    path("reviews", views.review_list, name="review_list"),
    path("reviews/<int:pk>", views.review_detail, name="review_detail"),
    path("reviews/pc/<int:pk>/new", views.review_create, name="review_create"),
    path("support", views.request_list, name="request_list"),
    path("support/new", views.request_create, name="request_create"),
    path("support/<int:pk>", views.request_detail, name="request_detail"),
    path("staff/support", views.staff_request_list, name="staff_request_list"),
    path("staff/support/<int:pk>", views.staff_request_detail, name="staff_request_detail"),
]
