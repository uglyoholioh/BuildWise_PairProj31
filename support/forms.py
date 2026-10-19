from django import forms

from orders.models import Order
from .models import SupportRequest


class SupportRequestForm(forms.Form):
    order = forms.ModelChoiceField(queryset=Order.objects.none())
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["order"].queryset = customer.orders.order_by("-placed_at")
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"


class SupportMessageForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    is_staff_only = forms.BooleanField(required=False, label="Staff-only note")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"


class CustomerMessageForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].widget.attrs["class"] = "form-control"


class SupportStatusForm(forms.Form):
    status = forms.ChoiceField(choices=(("open", "Open"), ("in_progress", "In progress"), ("closed", "Closed")))
    resolution_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, instance, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].initial = instance.status
        self.fields["resolution_note"].initial = instance.resolution_note
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("status") == "closed" and not cleaned_data.get("resolution_note", "").strip():
            self.add_error("resolution_note", "Enter a resolution note before closing the conversation.")
        return cleaned_data
