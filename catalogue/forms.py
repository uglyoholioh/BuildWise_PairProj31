from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from .compatibility import REQUIRED_SPECS
from .models import Accessory, Component, ComponentConflict, Configuration, ConfigurationItem


class ComponentForm(forms.ModelForm):
    price = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)

    class Meta:
        model = Component
        fields = (
            "category", "brand", "model", "description", "image", "source_url", "price", "active",
            "socket", "memory_type", "form_factor", "ml_tier", "specs",
        )
        help_texts = {
            "image": "Public product image URL.",
            "source_url": "Product page used for the listed details and price.",
        }
        widgets = {"specs": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input" if isinstance(field.widget, forms.CheckboxInput)
                else "form-select" if isinstance(field.widget, forms.Select)
                else "form-control"
            )

    def clean_specs(self):
        specs = self.cleaned_data["specs"]
        if specs is None:
            return {}
        if not isinstance(specs, dict):
            raise ValidationError('Enter a JSON object such as {"VRAM (GB)": 8}.')
        return specs

    def clean(self):
        cleaned_data = super().clean()
        for field in REQUIRED_SPECS.get(cleaned_data.get("category"), ()):
            if not cleaned_data.get(field):
                self.add_error(field, "This specification is required for the selected category.")
        return cleaned_data


class AccessoryForm(forms.ModelForm):
    price = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)

    class Meta:
        model = Accessory
        fields = ("name", "category", "image", "source_url", "price", "active")
        help_texts = {
            "image": "Public product image URL.",
            "source_url": "Product page used for the listed details and price.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input" if isinstance(field.widget, forms.CheckboxInput)
                else "form-control"
            )


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


class ConfigurationForm(forms.ModelForm):
    class Meta:
        model = Configuration
        fields = ("name", "description", "image", "source_url", "status")
        help_texts = {
            "image": "Enter an image URL or project-relative path.",
            "source_url": "Optional page used as the source for this preset.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class ConfigurationItemForm(forms.ModelForm):
    quantity = forms.IntegerField(min_value=1)

    class Meta:
        model = ConfigurationItem
        fields = ("component", "quantity")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["component"].queryset = Component.objects.order_by("category", "brand", "model")
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"


class BaseConfigurationItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        selected = [
            form.cleaned_data["component"]
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("component")
        ]
        if len(selected) != len(set(selected)):
            raise ValidationError("Choose each component once and set its quantity on that row.")


ConfigurationItemFormSet = inlineformset_factory(
    Configuration,
    ConfigurationItem,
    form=ConfigurationItemForm,
    formset=BaseConfigurationItemFormSet,
    extra=8,
    max_num=8,
    can_delete=True,
    validate_max=True,
)


class AssemblyFeeForm(forms.Form):
    fee_amount = forms.DecimalField(min_value=Decimal("0.00"), max_digits=10, decimal_places=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fee_amount"].widget.attrs["class"] = "form-control"
