from accounts.models import Customer
from .models import QuestionnaireResponse


def previous_response(request, customer=None):
    if customer is None and request.user.is_authenticated:
        customer = Customer.objects.filter(user=request.user).first()
    response_id = request.session.get("questionnaire_response_id")
    if response_id:
        response = QuestionnaireResponse.objects.filter(pk=response_id).first()
        if response and (response.customer_id is None or response.customer_id == getattr(customer, "pk", None)):
            return response
    return customer.questionnaires.order_by("-created_at", "-pk").first() if customer else None
