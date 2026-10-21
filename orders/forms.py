from django import forms

from catalogue.models import Component
from inventory.models import Reservation


class HoldPCForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"
        self.fields["reason"].label = "Why is this PC on hold?"


class QualityCheckForm(forms.Form):
    CHECKS = (
        ("installed_parts", "Installed parts match the order"),
        ("startup", "Successful startup"),
        ("stability", "Basic stability test"),
    )
    checklist_item = forms.ChoiceField(choices=CHECKS)
    passed = forms.ChoiceField(choices=(("yes", "Pass"), ("no", "Fail")))
    faulty_component = forms.ModelChoiceField(
        queryset=Reservation.objects.none(), required=False
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, purchased_pc, **kwargs):
        super().__init__(*args, **kwargs)
        component_ids = Reservation.objects.filter(
            purchased_pc=purchased_pc,
            status=Reservation.Status.RESERVED,
            component__isnull=False,
        ).values_list("component_id", flat=True)
        self.fields["faulty_component"].queryset = Component.objects.filter(pk__in=component_ids)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("passed") == "yes" and cleaned_data.get("faulty_component"):
            self.add_error("faulty_component", "Select a faulty component only for a failed check.")
        if cleaned_data.get("passed") == "no" and not cleaned_data.get("note", "").strip():
            self.add_error("note", "Record why this check failed.")
        return cleaned_data


class DispatchForm(forms.Form):
    courier = forms.CharField(required=False, max_length=100)
    tracking_ref = forms.CharField(required=False, max_length=120)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control form-control-sm"


class SubstitutionProposalForm(forms.Form):
    original_component = forms.ModelChoiceField(queryset=Component.objects.none())
    replacement_component = forms.ModelChoiceField(queryset=Component.objects.none())
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    spec_differences = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    fps_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, purchased_pc, **kwargs):
        super().__init__(*args, **kwargs)
        reserved_ids = Reservation.objects.filter(
            purchased_pc=purchased_pc,
            status=Reservation.Status.RESERVED,
            component__isnull=False,
        ).values_list("component_id", flat=True)
        self.fields["original_component"].queryset = Component.objects.filter(pk__in=reserved_ids)
        self.fields["replacement_component"].queryset = Component.objects.filter(active=True).order_by(
            "category", "brand", "model"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("original_component") == cleaned_data.get("replacement_component"):
            self.add_error("replacement_component", "Choose a different component as the replacement.")
        return cleaned_data
