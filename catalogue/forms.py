from decimal import Decimal

from django import forms

from .compatibility import REQUIRED_SPECS
from .models import Component, ComponentConflict


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


class ComponentConflictForm(forms.ModelForm):
    class Meta:
        model = ComponentConflict
        fields = ("component_a", "component_b", "reason")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean(self):
        cleaned_data = super().clean()
        component_a = cleaned_data.get("component_a")
        component_b = cleaned_data.get("component_b")
        if component_a and component_b:
            if component_a.pk == component_b.pk:
                self.add_error("component_b", "Choose two different components.")
            else:
                a_id, b_id = sorted((component_a.pk, component_b.pk))
                duplicate = ComponentConflict.objects.filter(
                    component_a_id=a_id, component_b_id=b_id
                ).exclude(pk=self.instance.pk)
                if duplicate.exists():
                    self.add_error("component_b", "This incompatibility is already recorded.")
        return cleaned_data
