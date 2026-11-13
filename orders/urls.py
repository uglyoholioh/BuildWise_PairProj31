from django.urls import path
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
    path("staff/queue", views.order_queue, name="order_queue"),
    path("staff/orders/<int:pk>/start", views.start_order, name="start_order"),
    path("staff/orders/<int:pk>/quality/start", views.start_quality_check, name="start_quality_check"),
    path("staff/orders/<int:pk>/quality/check", views.record_quality_check, name="record_quality_check"),
    path("staff/orders/<int:pk>/hold", views.hold_pc, name="hold_pc"),
    path("staff/orders/<int:pk>/pack", views.start_packing, name="start_packing"),
    path("staff/orders/<int:pk>/dispatch", views.dispatch_order, name="dispatch_order"),
    path("staff/orders/<int:pk>/deliver", views.staff_confirm_delivery, name="staff_confirm_delivery"),
    path("staff/pcs/<int:pk>/substitution", views.propose_substitution, name="propose_substitution"),
    path("staff/substitutions/<int:pk>/withdraw", views.withdraw_substitution, name="withdraw_substitution"),
    path("substitutions/<int:pk>/decision", views.decide_substitution, name="decide_substitution"),
]
