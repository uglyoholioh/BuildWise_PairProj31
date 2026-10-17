from django.urls import path
from accounts.views import staff_required
from . import views

app_name = "orders"

urlpatterns = [
    path("cart", views.placeholder, name="cart"),
    path("checkout", views.placeholder, name="checkout"),
    path("orders", views.placeholder, name="order_list"),
    path("orders/<int:pk>", views.placeholder, name="order_detail"),
    path("orders/<int:pk>/cancel", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/confirm-receipt", views.placeholder, name="confirm_receipt"),
    path("staff/queue", staff_required(views.order_queue), name="order_queue"),
    path("staff/orders/<int:pk>/start", staff_required(views.start_order), name="start_order"),
    path("staff/orders/<int:pk>/quality/start", staff_required(views.start_quality_check), name="start_quality_check"),
    path("staff/orders/<int:pk>/quality/check", staff_required(views.record_quality_check), name="record_quality_check"),
    path("staff/orders/<int:pk>/hold", staff_required(views.hold_pc), name="hold_pc"),
    path("staff/orders/<int:pk>/pack", staff_required(views.start_packing), name="start_packing"),
    path("staff/orders/<int:pk>/dispatch", staff_required(views.placeholder), name="dispatch_order"),
]
