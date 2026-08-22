from django import forms


class CheckoutForm(forms.Form):
    email = forms.EmailField(label="Электронная почта")
    email_confirmation = forms.EmailField(label="Повторите электронную почту")

    def clean(self) -> dict[str, str]:
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        email_confirmation = cleaned_data.get("email_confirmation")
        if email is None or email_confirmation is None:
            return cleaned_data
        normalized_email = email.casefold()
        normalized_confirmation = email_confirmation.casefold()
        if normalized_email != normalized_confirmation:
            self.add_error("email_confirmation", "Адреса электронной почты не совпадают.")
            return cleaned_data
        cleaned_data["email"] = normalized_email
        cleaned_data["email_confirmation"] = normalized_confirmation
        return cleaned_data
