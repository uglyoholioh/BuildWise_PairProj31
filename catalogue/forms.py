from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.forms import BaseInlineFormSet, inlineformset_factory

from .compatibility import REQUIRED_SPECS
from .models import Accessory, Component, ComponentConflict, Configuration, ConfigurationItem


def validate_image_upload(upload):
    # Existing FieldFiles and the clear checkbox need no upload validation.
    if upload and hasattr(upload, "image"):
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError("Choose an image no larger than 5 MB.")
        if upload.image.format not in ("JPEG", "PNG", "GIF", "WEBP"):
            raise ValidationError("Choose a JPEG, PNG, GIF or WebP image.")
    return upload


class ComponentForm(forms.ModelForm):
    price = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)

    class Meta:
        model = Component
        fields = (
            "category", "brand", "model", "description", "image_file", "image", "source_url", "price", "active",
            "socket", "memory_type", "form_factor", "requires_cpu_cooler", "ml_tier", "specs",
        )
        labels = {"image_file": "Upload image", "image": "External image URL", "requires_cpu_cooler": "CPU requires a separate cooler"}
        help_texts = {
            "image_file": "JPEG, PNG, GIF or WebP, up to 5 MB. An uploaded image takes priority over the URL.",
            "image": "Optional public image URL. Used when no image is uploaded.",
            "requires_cpu_cooler": "For CPUs supplied without an included cooler. Builds using this CPU must include a CPU cooler.",
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

    def clean_image_file(self):
        return validate_image_upload(self.cleaned_data["image_file"])

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
        if cleaned_data.get("requires_cpu_cooler") and cleaned_data.get("category") != Component.Category.CPU:
            self.add_error("requires_cpu_cooler", "Only a CPU can require a separate CPU cooler.")
        return cleaned_data


class AccessoryForm(forms.ModelForm):
    price = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)

    class Meta:
        model = Accessory
        fields = ("name", "category", "image_file", "image", "source_url", "price", "active")
        labels = {"image_file": "Upload image", "image": "External image URL"}
        help_texts = {
            "image_file": "JPEG, PNG, GIF or WebP, up to 5 MB. An uploaded image takes priority over the URL.",
            "image": "Optional public image URL. Used when no image is uploaded.",
            "source_url": "Product page used for the listed details and price.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input" if isinstance(field.widget, forms.CheckboxInput)
                else "form-control"
            )

    def clean_image_file(self):
        return validate_image_upload(self.cleaned_data["image_file"])


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
        fields = ("name", "description", "image_file", "image", "source_url", "status")
        labels = {"image_file": "Upload image", "image": "Image URL or static path"}
        help_texts = {
            "image_file": "JPEG, PNG, GIF or WebP, up to 5 MB. An uploaded image takes priority over the URL or path.",
            "image": "Optional public image URL or a path inside the project's static folder, such as images/pc.png.",
            "source_url": "Optional page used as the source for this preset.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean_image_file(self):
        return validate_image_upload(self.cleaned_data["image_file"])

    def clean_image(self):
        image = self.cleaned_data["image"].strip()
        if image.startswith(("https://", "http://")):
            URLValidator(schemes=("http", "https"))(image)
        elif image and (":" in image or "\\" in image or image.startswith("//") or ".." in image.split("/")):
            raise ValidationError("Enter an HTTP(S) image URL or a static image path.")
        return image


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
        existing_ids = set(self.get_queryset().values_list("pk", flat=True))
        submitted_ids = [form.instance.pk for form in self.initial_forms]
        if len(submitted_ids) != len(existing_ids) or set(submitted_ids) != existing_ids:
            raise ValidationError("The preset rows have changed. Reload this page before saving.")
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


class BuildForm(forms.Form):
    name = forms.CharField(max_length=160, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for category, label in Component.Category.choices:
            self.fields[f"component_{category}"] = forms.ModelChoiceField(
                queryset=Component.objects.filter(category=category, active=True),
                required=False,
                label=label,
            )


class PresetFilterForm(forms.Form):
    min_price = forms.DecimalField(min_value=0, max_digits=16, decimal_places=2, required=False)
    max_price = forms.DecimalField(min_value=0, max_digits=16, decimal_places=2, required=False)
    tier = forms.ChoiceField(required=False)
    availability = forms.ChoiceField(
        choices=(("all", "All"), ("in_stock", "In stock"), ("out_of_stock", "Out of stock")),
        required=False,
    )

    def __init__(self, *args, tiers, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tier"].choices = [(tier, tier) for tier in tiers]

    def clean(self):
        data = super().clean()
        minimum, maximum = data.get("min_price"), data.get("max_price")
        if minimum is not None and maximum is not None and minimum > maximum:
            self.add_error("max_price", "Maximum price must be at least the minimum price.")
        return data
