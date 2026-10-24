from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User

from .models import Customer


class CustomerRegistrationForm(UserCreationForm):
    name = forms.CharField(max_length=160)
    email = forms.EmailField()
    delivery_address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    class Meta:
        model = User
        fields = ("username", "name", "email", "delivery_address")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if Customer.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            Customer.objects.create(
                user=user,
                name=self.cleaned_data["name"],
                email=self.cleaned_data["email"],
                delivery_address=self.cleaned_data["delivery_address"],
            )
        return user


class CustomerAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not Customer.objects.filter(user=user).exists():
            raise forms.ValidationError("Username or password is incorrect.", code="invalid_login")


class CustomerProfileForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ("name", "email", "delivery_address")
        widgets = {"delivery_address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
