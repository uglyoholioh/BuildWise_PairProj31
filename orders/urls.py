from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    path("cart", views.placeholder, name="cart"),
    path("checkout", views.placeholder, name="checkout"),
    path("orders", views.placeholder, name="order_list"),
    path("orders/<int:pk>", views.placeholder, name="order_detail"),
    path("orders/<int:pk>/cancel", views.placeholder, name="order_cancel"),
    path("orders/<int:pk>/confirm-receipt", views.placeholder, name="confirm_receipt"),
    path("panel/queue", views.placeholder, name="order_queue"),
    path("panel/orders/<int:pk>/start", views.placeholder, name="start_order"),
    path("panel/orders/<int:pk>/dispatch", views.placeholder, name="dispatch_order"),
]
