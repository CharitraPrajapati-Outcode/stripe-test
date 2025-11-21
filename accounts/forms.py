from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import get_user_model


def add_form_control(classes_dict):
    """Helper to ensure widget has form-control class"""
    attrs = classes_dict.get('attrs', {})
    existing = attrs.get('class', '')
    if 'form-control' not in existing:
        attrs['class'] = (existing + ' form-control').strip()
    classes_dict['attrs'] = attrs
    return classes_dict


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = get_user_model()
        fields = ('username', 'email')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget_attrs = field.widget.attrs if hasattr(field.widget, 'attrs') else {}
            widget_attrs.setdefault('class', 'form-control')
            field.widget.attrs = widget_attrs


class CustomAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget_attrs = field.widget.attrs if hasattr(field.widget, 'attrs') else {}
            widget_attrs.setdefault('class', 'form-control')
            field.widget.attrs = widget_attrs
