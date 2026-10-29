from django.urls import path
from accounts.views import staff_required
from . import views

app_name = "orders"

urlpatterns = [
    path("cart", views.cart, name="cart"),
    path("cart/add", views.cart_add, name="cart_add"),
    path("checkout", views.checkout, name="checkout"),
    path("orders", views.order_list, name="order_list"),
    path("orders/<int:pk>", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/cancel", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/confirm-receipt", views.confirm_receipt, name="confirm_receipt"),
    path("staff/queue", staff_required(views.order_queue), name="order_queue"),
    path("staff/orders/<int:pk>/start", staff_required(views.start_order), name="start_order"),
    path("staff/orders/<int:pk>/quality/start", staff_required(views.start_quality_check), name="start_quality_check"),
    path("staff/orders/<int:pk>/quality/check", staff_required(views.record_quality_check), name="record_quality_check"),
    path("staff/orders/<int:pk>/hold", staff_required(views.hold_pc), name="hold_pc"),
    path("staff/orders/<int:pk>/pack", staff_required(views.start_packing), name="start_packing"),
    path("staff/orders/<int:pk>/dispatch", staff_required(views.dispatch_order), name="dispatch_order"),
    path("staff/orders/<int:pk>/deliver", staff_required(views.staff_confirm_delivery), name="staff_confirm_delivery"),
    path("staff/pcs/<int:pk>/substitution", staff_required(views.propose_substitution), name="propose_substitution"),
    path("substitutions/<int:pk>/decision", views.decide_substitution, name="decide_substitution"),
]
