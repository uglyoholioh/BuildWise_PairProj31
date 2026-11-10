from django import forms

from catalogue.models import Accessory, Component
from .models import StockAdjustment


class StockAdjustmentForm(forms.ModelForm):
    delta = forms.IntegerField(
        label="Quantity change",
        help_text="Use a positive number for a receipt and a negative number for a stock correction.",
    )

    class Meta:
        model = StockAdjustment
        fields = ("item_type", "component", "accessory", "delta", "reason")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["component"].queryset = Component.objects.order_by("category", "brand", "model")
        self.fields["accessory"].queryset = Accessory.objects.order_by("category", "name")
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean(self):
        cleaned_data = super().clean()
        item_type = cleaned_data.get("item_type")
        component = cleaned_data.get("component")
        accessory = cleaned_data.get("accessory")
        if not cleaned_data.get("delta"):
            self.add_error("delta", "Quantity change cannot be zero.")
        if item_type == "component" and (component is None or accessory is not None):
            self.add_error("component", "Select exactly one component for this adjustment.")
        elif item_type == "accessory" and (accessory is None or component is not None):
            self.add_error("accessory", "Select exactly one accessory for this adjustment.")
        return cleaned_data


class CancelledStockInspectionForm(forms.Form):
    quantity = forms.IntegerField(min_value=1, max_value=32767)
    outcome = forms.ChoiceField(choices=(("return", "Return to stock"), ("discard", "Write off")))
