from django import forms


class HoldPCForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"
        self.fields["reason"].label = "Why is this PC on hold?"
