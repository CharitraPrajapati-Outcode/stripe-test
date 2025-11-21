from django.views.generic import CreateView, TemplateView, ListView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
import stripe
from decimal import Decimal

from .forms import CustomUserCreationForm
from django.views import View
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages

from billing.models import SubscriptionPlan, UserSubscription, SubscriptionPayment
from django.utils import timezone
from datetime import datetime


def _to_dt(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        return None


class RegisterView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('accounts:login')


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Fetch available subscription prices (plans) from Stripe
        plans = []
        secret = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if secret:
            try:
                stripe.api_key = secret
                # Expand product to get human readable product name
                resp = stripe.Price.list(active=True, limit=100, expand=['data.product'])
                for p in resp.data:
                    product = p.product if hasattr(p, 'product') else None
                    product_name = ''
                    if isinstance(product, dict):
                        product_name = product.get('name') or product.get('id')
                    else:
                        product_name = getattr(product, 'name', '') if product else ''

                    unit_amount = getattr(p, 'unit_amount_decimal', None) or getattr(p, 'unit_amount', None)
                    if unit_amount is None:
                        # fallback
                        amount = '0.00'
                    else:
                        # unit_amount_decimal is in smallest currency unit depending on API; stripe returns decimal string
                        try:
                            # Stripe returns unit_amount_decimal as string in cents when provided; normalize to decimal
                            amt = int(p.unit_amount) / 100.0 if getattr(p, 'unit_amount', None) is not None else float(p.unit_amount_decimal)
                            amount = f"{amt:.2f}"
                        except Exception:
                            amount = str(unit_amount)

                    interval = ''
                    if getattr(p, 'recurring', None):
                        interval = p.recurring.get('interval')

                    # Ensure a local SubscriptionPlan exists or is updated
                    try:
                        # derive decimal price from unit_amount (cents) when available
                        if getattr(p, 'unit_amount', None) is not None:
                            price_decimal = Decimal(p.unit_amount) / Decimal(100)
                        elif getattr(p, 'unit_amount_decimal', None) is not None:
                            price_decimal = Decimal(str(p.unit_amount_decimal))
                        else:
                            price_decimal = Decimal('0.00')

                        sp_defaults = {
                            'name': product_name or p.id,
                            'price': price_decimal,
                            'interval': interval or 'month',
                            'active': True,
                        }
                        SubscriptionPlan.objects.update_or_create(stripe_price_id=p.id, defaults=sp_defaults)
                    except Exception:
                        # don't fail if DB sync fails; continue building UI list
                        pass

                    plans.append({
                        'id': p.id,
                        'product_name': product_name,
                        'amount': amount,
                        'currency': getattr(p, 'currency', '').upper(),
                        'interval': interval,
                        'stripe_price': p,
                    })
            except Exception as e:
                # don't fail the page if Stripe call fails; log to console via context for debugging
                context['stripe_error'] = str(e)

        # Try to fetch Account (organization) information
        account_name = None
        try:
            if secret:
                acct = stripe.Account.retrieve()
                # Prefer business_profile.name, fallback to settings or display_name
                bp = acct.get('business_profile') if isinstance(acct, dict) else getattr(acct, 'business_profile', None)
                if bp:
                    account_name = bp.get('name') if isinstance(bp, dict) else getattr(bp, 'name', None)
                if not account_name:
                    account_name = acct.get('settings', {}).get('dashboard', {}).get('display_name') if isinstance(acct, dict) else getattr(acct, 'display_name', None)
        except Exception:
            # ignore account fetch errors; don't expose sensitive details
            account_name = None

        context['plans'] = plans
        context['stripe_account_name'] = account_name
        # Refresh local subscription statuses from Stripe where possible
        if secret:
            try:
                for usub in self.request.user.subscriptions.all():
                    if not usub.stripe_subscription_id:
                        continue
                    try:
                        remote = stripe.Subscription.retrieve(usub.stripe_subscription_id)
                        # pull status and period dates
                        r_status = remote.get('status') if isinstance(remote, dict) else getattr(remote, 'status', None)
                        r_cps = remote.get('current_period_start') if isinstance(remote, dict) else getattr(remote, 'current_period_start', None)
                        r_cpe = remote.get('current_period_end') if isinstance(remote, dict) else getattr(remote, 'current_period_end', None)
                        r_cancel_at_period_end = remote.get('cancel_at_period_end') if isinstance(remote, dict) else getattr(remote, 'cancel_at_period_end', None)
                        r_canceled_at = remote.get('canceled_at') if isinstance(remote, dict) else getattr(remote, 'canceled_at', None)
                        updated = False
                        if r_status and r_status != usub.status:
                            usub.status = r_status
                            updated = True
                        # convert timestamps
                        new_cps = _to_dt(r_cps)
                        new_cpe = _to_dt(r_cpe)
                        if new_cps and new_cps != usub.current_period_start:
                            usub.current_period_start = new_cps
                            updated = True
                        if new_cpe and new_cpe != usub.current_period_end:
                            usub.current_period_end = new_cpe
                            updated = True
                        if r_cancel_at_period_end is not None and r_cancel_at_period_end != usub.cancel_at_period_end:
                            usub.cancel_at_period_end = r_cancel_at_period_end
                            updated = True
                        # if Stripe provides a canceled_at timestamp, persist it
                        if r_canceled_at:
                            canceled_dt = _to_dt(r_canceled_at)
                            if canceled_dt and canceled_dt != usub.cancelled_at:
                                usub.cancelled_at = canceled_dt
                                updated = True
                        if updated:
                            usub.save()
                    except Exception:
                        # ignore per-subscription errors
                        pass
            except Exception:
                # ignore dashboard sync errors
                pass
        # Split subscriptions into active list for dashboard UI
        try:
            all_subs = self.request.user.subscriptions.all()
            active_subs = all_subs.filter(status__in=['active', 'trialing'])
            context['active_subscriptions'] = active_subs
        except Exception:
            context['active_subscriptions'] = []

        return context


class UserSubscriptionListView(LoginRequiredMixin, ListView):
    """ListView to show the current user's subscriptions split into active and past/cancelled."""
    model = UserSubscription
    template_name = 'subscriptions_list.html'
    context_object_name = 'subscriptions'

    def get_queryset(self):
        # return all subscriptions for the current user (ordered newest first)
        return UserSubscription.objects.filter(user=self.request.user).order_by('-created_at')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        ctx['active_subscriptions'] = qs.filter(status__in=['active', 'trialing'])
        ctx['cancelled_subscriptions'] = qs.filter(status='canceled')
        return ctx


class SubscribeView(LoginRequiredMixin, View):
    def get(self, request, price_id):
        secret = getattr(settings, 'STRIPE_SECRET_KEY', None)
        publishable = getattr(settings, 'STRIPE_PUBLISHABLE_KEY', '')

        if not secret:
            messages.error(request, 'Stripe secret key not configured.')
            return redirect('accounts:dashboard')

        stripe.api_key = secret

        user = request.user
        # Ensure customer exists
        if not getattr(user, 'stripe_customer_id', None):
            cust = stripe.Customer.create(email=user.email, name=user.get_full_name() or user.username)
            user.stripe_customer_id = cust.id
            user.save()
        customer_id = user.stripe_customer_id

        # List existing payment methods
        pms = []
        try:
            pm_list = stripe.PaymentMethod.list(customer=customer_id, type='card')
            for m in pm_list.data:
                pms.append({'id': m.id, 'brand': m.card.brand, 'last4': m.card.last4, 'exp_month': m.card.exp_month, 'exp_year': m.card.exp_year})
        except Exception:
            pms = []

        # Create a SetupIntent for new card entry
        setup_intent = stripe.SetupIntent.create(customer=customer_id)

        # Retrieve price info for display
        try:
            price = stripe.Price.retrieve(price_id, expand=['product'])
        except Exception:
            messages.error(request, 'Invalid price id.')
            return redirect('accounts:dashboard')
        # Compute a human readable amount (Stripe stores amounts in cents)
        try:
            if getattr(price, 'unit_amount', None) is not None:
                price_amount = Decimal(int(price.unit_amount)) / Decimal(100)
            elif getattr(price, 'unit_amount_decimal', None) is not None:
                price_amount = Decimal(str(price.unit_amount_decimal))
            else:
                price_amount = Decimal('0.00')
        except Exception:
            price_amount = Decimal('0.00')

        context = {
            'price_id': price_id,
            'price': price,
            'price_amount': f"{price_amount:.2f}",
            'price_currency': getattr(price, 'currency', '').upper(),
            'payment_methods': pms,
            'setup_client_secret': setup_intent.client_secret,
            'stripe_publishable_key': publishable,
        }
        return render(request, 'subscribe.html', context)


class CreateSubscriptionView(LoginRequiredMixin, View):
    def post(self, request):
        price_id = request.POST.get('price_id')
        payment_method = request.POST.get('payment_method')
        secret = getattr(settings, 'STRIPE_SECRET_KEY', None)

        if not secret:
            messages.error(request, 'Stripe secret key not configured.')
            return redirect('accounts:dashboard')

        stripe.api_key = secret
        user = request.user
        if not getattr(user, 'stripe_customer_id', None):
            # create customer if somehow missing
            cust = stripe.Customer.create(email=user.email, name=user.get_full_name() or user.username)
            user.stripe_customer_id = cust.id
            user.save()

        customer_id = user.stripe_customer_id

        try:
            # Ensure payment method is attached to customer
            if payment_method:
                # If it's not already attached, attach it (Stripe will throw if already attached)
                try:
                    stripe.PaymentMethod.attach(payment_method, customer=customer_id)
                except Exception:
                    # ignore attach errors (already attached etc.)
                    pass

            # Create the subscription using the chosen payment method
            sub = stripe.Subscription.create(
                customer=customer_id,
                items=[{'price': price_id}],
                default_payment_method=payment_method,
                expand=['latest_invoice.payment_intent']
            )

            # Retrieve or create local SubscriptionPlan
            try:
                plan_obj = SubscriptionPlan.objects.get(stripe_price_id=price_id)
            except SubscriptionPlan.DoesNotExist:
                # fetch price to populate
                price = stripe.Price.retrieve(price_id, expand=['product'])
                prod = price.product if hasattr(price, 'product') else None
                prod_name = prod.get('name') if isinstance(prod, dict) else getattr(prod, 'name', None) if prod else price.id
                unit_amount = getattr(price, 'unit_amount', 0) or 0
                amount_decimal = (int(unit_amount) / 100.0) if unit_amount else 0
                plan_obj = SubscriptionPlan.objects.create(
                    name=prod_name or price.id,
                    stripe_price_id=price_id,
                    price=amount_decimal,
                    interval=(price.recurring.get('interval') if getattr(price, 'recurring', None) else 'month')
                )

            # Save UserSubscription (extract timestamps whether Stripe returned dict or object)
            from datetime import datetime, timezone

            def _to_dt(value):
                if not value:
                    return None
                try:
                    # Stripe returns timestamps in seconds
                    return datetime.fromtimestamp(int(value), tz=timezone.utc)
                except Exception:
                    return None

            # subscription object may be dict-like or have attributes
            raw_cps = None
            raw_cpe = None

            print(sub)
            if isinstance(sub, dict):
                raw_cps = sub.get('current_period_start') or sub.get('start_date')
                raw_cpe = sub.get('current_period_end')
                # some Stripe responses place period timestamps under items.data[0]
                if (not raw_cps or not raw_cpe) and sub.get('items'):
                    items = sub.get('items', {}).get('data') if isinstance(sub.get('items'), dict) else sub.get('items')
                    if items and isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                        raw_cps = raw_cps or items[0].get('current_period_start') or items[0].get('start_date')
                        raw_cpe = raw_cpe or items[0].get('current_period_end')
            else:
                raw_cps = getattr(sub, 'current_period_start', None) or getattr(sub, 'start_date', None)
                raw_cpe = getattr(sub, 'current_period_end', None) or getattr(sub, 'end_date', None)
                # try nested items.data[0]
                try:
                    items = getattr(sub, 'items', None)
                    data = getattr(items, 'data', None) if items else None
                    if data and len(data) > 0:
                        first = data[0]
                        raw_cps = raw_cps or (first.get('current_period_start') if isinstance(first, dict) else getattr(first, 'current_period_start', None)) or (first.get('start_date') if isinstance(first, dict) else getattr(first, 'start_date', None))
                        raw_cpe = raw_cpe or (first.get('current_period_end') if isinstance(first, dict) else getattr(first, 'current_period_end', None))
                except Exception:
                    pass

            usub = UserSubscription.objects.create(
                user=user,
                plan=plan_obj,
                stripe_subscription_id=(sub.get('id') if isinstance(sub, dict) else getattr(sub, 'id', None)),
                status=(sub.get('status') if isinstance(sub, dict) else getattr(sub, 'status', None)),
                current_period_start=_to_dt(raw_cps),
                current_period_end=_to_dt(raw_cpe),
            )

            # Record payment info and ensure subscription dates/status are saved locally
            try:
                invoice = None
                # latest_invoice may be an id string, an object, or absent
                raw_invoice = sub.get('latest_invoice') if isinstance(sub, dict) else getattr(sub, 'latest_invoice', None)
                if raw_invoice:
                    if isinstance(raw_invoice, str):
                        invoice = stripe.Invoice.retrieve(raw_invoice, expand=['payment_intent', 'payment_intent.charges'])
                    elif isinstance(raw_invoice, dict):
                        # if dict but not expanded, try to retrieve fully
                        inv_id = raw_invoice.get('id')
                        if inv_id and not raw_invoice.get('payment_intent'):
                            invoice = stripe.Invoice.retrieve(inv_id, expand=['payment_intent', 'payment_intent.charges'])
                        else:
                            invoice = raw_invoice
                    else:
                        invoice = raw_invoice

                payment_intent = None
                amount_paid = None
                currency = None
                charge_id = None
                pi_status = ''

                if invoice:
                    # extract amount and currency from invoice
                    amount_paid = invoice.get('amount_paid') if isinstance(invoice, dict) else getattr(invoice, 'amount_paid', None)
                    currency = (invoice.get('currency') if isinstance(invoice, dict) else getattr(invoice, 'currency', None) or '').upper()

                    raw_pi = invoice.get('payment_intent') if isinstance(invoice, dict) else getattr(invoice, 'payment_intent', None)
                    if raw_pi:
                        if isinstance(raw_pi, str):
                            payment_intent = stripe.PaymentIntent.retrieve(raw_pi, expand=['charges'])
                        else:
                            payment_intent = raw_pi

                    # try to get charge id
                    if payment_intent:
                        charges = None
                        if hasattr(payment_intent, 'charges'):
                            charges = getattr(payment_intent.charges, 'data', None)
                        elif isinstance(payment_intent, dict):
                            charges = payment_intent.get('charges', {}).get('data')
                        if charges:
                            first = charges[0]
                            charge_id = getattr(first, 'id', None) if not isinstance(first, dict) else first.get('id')
                            pi_status = getattr(payment_intent, 'status', None) if not isinstance(payment_intent, dict) else payment_intent.get('status')

                # create payment record if there was an amount paid or payment intent
                if amount_paid or payment_intent:
                    try:
                        amt = (int(amount_paid) / 100.0) if isinstance(amount_paid, (int, str)) and str(amount_paid).isdigit() else (float(amount_paid) if amount_paid else 0)
                    except Exception:
                        amt = 0

                    SubscriptionPayment.objects.create(
                        subscription=usub,
                        user=user,
                        amount=amt,
                        currency=(currency or '').upper(),
                        stripe_payment_intent_id=(getattr(payment_intent, 'id', None) if payment_intent else None) or (payment_intent.get('id') if isinstance(payment_intent, dict) else None),
                        stripe_charge_id=charge_id,
                        status=pi_status or ''
                    )
            except Exception:
                # swallow errors to avoid breaking subscription creation flow
                pass

            # update local subscription record with latest status and period dates (only when present)
            try:
                new_status = (sub.get('status') if isinstance(sub, dict) else getattr(sub, 'status', None))
                if new_status:
                    usub.status = new_status

                # try to extract period timestamps from subscription or nested items
                new_cps = None
                new_cpe = None
                if isinstance(sub, dict):
                    new_cps = sub.get('current_period_start') or sub.get('start_date')
                    new_cpe = sub.get('current_period_end')
                    if (not new_cps or not new_cpe) and sub.get('items'):
                        items = sub.get('items', {}).get('data') if isinstance(sub.get('items'), dict) else sub.get('items')
                        if items and isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                            new_cps = new_cps or items[0].get('current_period_start') or items[0].get('start_date')
                            new_cpe = new_cpe or items[0].get('current_period_end')
                else:
                    new_cps = getattr(sub, 'current_period_start', None) or getattr(sub, 'start_date', None)
                    new_cpe = getattr(sub, 'current_period_end', None)
                    try:
                        items = getattr(sub, 'items', None)
                        data = getattr(items, 'data', None) if items else None
                        if data and len(data) > 0:
                            first = data[0]
                            new_cps = new_cps or (first.get('current_period_start') if isinstance(first, dict) else getattr(first, 'current_period_start', None)) or (first.get('start_date') if isinstance(first, dict) else getattr(first, 'start_date', None))
                            new_cpe = new_cpe or (first.get('current_period_end') if isinstance(first, dict) else getattr(first, 'current_period_end', None))
                    except Exception:
                        pass

                if new_cps:
                    usub.current_period_start = _to_dt(new_cps)
                if new_cpe:
                    usub.current_period_end = _to_dt(new_cpe)

                usub.save()
            except Exception:
                pass

            messages.success(request, 'Subscription created successfully.')
            return redirect('accounts:dashboard')

        except stripe.error.CardError as e:
            messages.error(request, f'Card error: {e.user_message or str(e)}')
            return redirect('accounts:subscribe', price_id=price_id)
        except Exception as e:
            messages.error(request, f'Error creating subscription: {str(e)}')
            return redirect('accounts:subscribe', price_id=price_id)


class CancelSubscriptionView(LoginRequiredMixin, View):
    """Cancel a Stripe subscription either immediately or at period end."""

    def post(self, request, sub_id):
        secret = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if not secret:
            messages.error(request, 'Stripe secret key not configured.')
            return redirect('accounts:dashboard')

        stripe.api_key = secret
        user = request.user

        # Ensure this subscription belongs to the user locally (best-effort)
        usub = UserSubscription.objects.filter(stripe_subscription_id=sub_id, user=user).first()
        if not usub:
            messages.error(request, 'Subscription not found for current user.')
            return redirect('accounts:dashboard')

        when = request.POST.get('when', 'period_end')

        try:
            if when == 'now':
                # Cancel immediately (if not already cancelled)
                if usub.status == 'canceled':
                    messages.info(request, 'Subscription is already cancelled.')
                else:
                    cancelled = stripe.Subscription.delete(sub_id)
                    # Update local record
                    usub.status = cancelled.get('status') if isinstance(cancelled, dict) else getattr(cancelled, 'status', 'canceled')
                    usub.cancel_at_period_end = False
                    usub.current_period_end = _to_dt(cancelled.get('current_period_end') if isinstance(cancelled, dict) else getattr(cancelled, 'current_period_end', None))
                    # record when the cancellation was requested / took effect
                    try:
                        usub.cancelled_at = timezone.now()
                    except Exception:
                        pass
                    usub.save()
                    messages.success(request, 'Subscription cancelled immediately.')
            else:
                # Cancel at period end
                if usub.cancel_at_period_end:
                    messages.info(request, 'Subscription is already set to cancel at period end.')
                else:
                    updated = stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
                    usub.cancel_at_period_end = True
                    usub.status = updated.get('status') if isinstance(updated, dict) else getattr(updated, 'status', usub.status)
                    # usub.current_period_end = _to_dt(updated.get('current_period_end') if isinstance(updated, dict) else getattr(updated, 'current_period_end', None))
                    # record when the user requested cancellation (not the effective end date)
                    try:
                        usub.cancelled_at = timezone.now()
                    except Exception:
                        pass
                    usub.save()
                    messages.success(request, 'Subscription will be cancelled at period end.')
        except Exception as e:
            messages.error(request, f'Error cancelling subscription: {str(e)}')

        return redirect('accounts:dashboard')



