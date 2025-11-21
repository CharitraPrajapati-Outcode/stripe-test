from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.utils import timezone


class User(AbstractUser):
    """Custom user model that stores Stripe customer id for payment linking."""
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)


class SubscriptionPlan(models.Model):
    INTERVAL_CHOICES = [
        ('month', 'Monthly'),
        ('year', 'Yearly'),
    ]

    name = models.CharField(max_length=200)
    stripe_price_id = models.CharField(max_length=255, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES, default='month')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class UserSubscription(models.Model):
    STATUS_CHOICES = [
        ('trialing', 'Trialing'),
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('canceled', 'Canceled'),
        ('incomplete', 'Incomplete'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='incomplete')
    current_period_start = models.DateTimeField(blank=True, null=True)
    current_period_end = models.DateTimeField(blank=True, null=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} - {self.plan.name} ({self.status})"


class SubscriptionPayment(models.Model):
    subscription = models.ForeignKey(UserSubscription, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default='usd')
    stripe_invoice_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_charge_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=30, default='succeeded')
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user} - {self.amount} {self.currency} ({self.status})"

