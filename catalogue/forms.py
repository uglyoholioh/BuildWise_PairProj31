from decimal import Decimal

from django import forms

from .models import Component


REQUIRED_SPECS = {
    Component.Category.CPU: ("socket",),
    Component.Category.MOTHERBOARD: ("socket", "memory_type", "form_factor"),
    Component.Category.MEMORY: ("memory_type",),
    Component.Category.CASE: ("form_factor",),
}


class ComponentForm(forms.ModelForm):
    price = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)

    class Meta:
        model = Component
        fields = (
            "category", "brand", "model", "description", "price", "active",
            "socket", "memory_type", "form_factor", "ml_tier", "specs",
        )
        widgets = {"specs": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input" if isinstance(field.widget, forms.CheckboxInput)
                else "form-select" if isinstance(field.widget, forms.Select)
                else "form-control"
            )

    def clean(self):
        cleaned_data = super().clean()
        for field in REQUIRED_SPECS.get(cleaned_data.get("category"), ()):
            if not cleaned_data.get(field):
                self.add_error(field, "This specification is required for the selected category.")
        return cleaned_data
