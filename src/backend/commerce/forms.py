from django import forms


class CheckoutForm(forms.Form):
    email = forms.EmailField(label="Электронная почта")

    def clean_email(self) -> str:
        return self.cleaned_data["email"].casefold()
