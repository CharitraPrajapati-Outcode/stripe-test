from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import datetime
import stripe
from decimal import Decimal

from billing.models import SubscriptionPlan, UserSubscription, SubscriptionPayment
from django.contrib.auth import get_user_model


User = get_user_model()


def _to_dt(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        return None


class Command(BaseCommand):
    help = 'Sync subscriptions and payments from Stripe into local DB'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100, help='Number of Stripe subscriptions to fetch per page')
        parser.add_argument('--dry-run', action='store_true', help='Do not write to DB; only print actions')

    def handle(self, *args, **options):
        secret = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if not secret:
            self.stderr.write('STRIPE_SECRET_KEY not configured in settings')
            return

        stripe.api_key = secret
        limit = options.get('limit') or 100
        dry = options.get('dry_run')

        self.stdout.write('Starting Stripe sync...')

        try:
            # Avoid expanding too deeply (stripe limits expansion depth).
            # Expand the latest invoice's payment_intent and the price object on items.
            subs_iter = stripe.Subscription.list(
                limit=limit,
                expand=['data.latest_invoice.payment_intent', 'data.items.data.price']
            ).auto_paging_iter()
        except Exception as e:
            self.stderr.write(f'Failed to list subscriptions: {e}')
            return

        count = 0
        for s in subs_iter:
            count += 1
            try:
                # get customer and map to local user
                cust_id = None
                if isinstance(s, dict):
                    cust_id = s.get('customer')
                else:
                    cust_id = getattr(s, 'customer', None)

                user = None
                if cust_id:
                    user = User.objects.filter(stripe_customer_id=cust_id).first()

                # determine price id
                price_id = None
                price_obj = None
                try:
                    items = s['items']['data'] if isinstance(s, dict) else getattr(s.items, 'data', None)
                    if items and len(items) > 0:
                        price_obj = items[0].get('price') if isinstance(items[0], dict) else getattr(items[0].price, None)
                        if isinstance(price_obj, dict):
                            price_id = price_obj.get('id')
                        else:
                            price_id = getattr(price_obj, 'id', None)
                except Exception:
                    price_id = None

                # ensure local SubscriptionPlan exists
                plan_obj = None
                if price_id:
                    try:
                        # compute price decimal
                        unit_amount = None
                        if isinstance(price_obj, dict):
                            unit_amount = price_obj.get('unit_amount') or price_obj.get('unit_amount_decimal')
                        else:
                            unit_amount = getattr(price_obj, 'unit_amount', None) or getattr(price_obj, 'unit_amount_decimal', None)

                        if unit_amount is None:
                            price_decimal = Decimal('0.00')
                        else:
                            try:
                                price_decimal = Decimal(int(unit_amount)) / Decimal(100)
                            except Exception:
                                # if it's decimal string
                                price_decimal = Decimal(str(unit_amount))

                        prod_name = None
                        # price_obj.product may be a string id or an expanded dict.
                        prod = None
                        if isinstance(price_obj, dict):
                            prod = price_obj.get('product')
                        else:
                            prod = getattr(price_obj, 'product', None)

                        # If product is an expanded dict, take its name.
                        if isinstance(prod, dict):
                            prod_name = prod.get('name')
                        else:
                            # If it's a string id, retrieve product from Stripe (best-effort).
                            try:
                                if prod:
                                    prod_obj = stripe.Product.retrieve(prod)
                                    prod_name = prod_obj.get('name') if isinstance(prod_obj, dict) else getattr(prod_obj, 'name', None)
                            except Exception:
                                # fallback to using the price id as name when product retrieval fails
                                prod_name = None

                        sp_defaults = {'name': prod_name or price_id, 'price': price_decimal, 'interval': (price_obj.get('recurring', {}).get('interval') if isinstance(price_obj, dict) else (getattr(price_obj, 'recurring', None).get('interval') if getattr(price_obj, 'recurring', None) else 'month')), 'active': True}
                        if dry:
                            self.stdout.write(f'[DRY] Would update_or_create plan {price_id} -> {sp_defaults}')
                        else:
                            plan_obj, _ = SubscriptionPlan.objects.update_or_create(stripe_price_id=price_id, defaults=sp_defaults)
                    except Exception as e:
                        self.stderr.write(f'Failed to sync plan {price_id}: {e}')

                # upsert UserSubscription
                try:
                    sub_id = s['id'] if isinstance(s, dict) else getattr(s, 'id', None)
                    status = s.get('status') if isinstance(s, dict) else getattr(s, 'status', None)
                    cps = s.get('current_period_start') if isinstance(s, dict) else getattr(s, 'current_period_start', None)
                    cpe = s.get('current_period_end') if isinstance(s, dict) else getattr(s, 'current_period_end', None)

                    cps_dt = _to_dt(cps)
                    cpe_dt = _to_dt(cpe)

                    if dry:
                        self.stdout.write(f'[DRY] Would upsert subscription {sub_id} for user {user} plan {plan_obj}')
                    else:
                        usub, created = UserSubscription.objects.update_or_create(
                            stripe_subscription_id=sub_id,
                            defaults={
                                'user': user,
                                'plan': plan_obj,
                                'status': status or 'active',
                                'current_period_start': cps_dt,
                                'current_period_end': cpe_dt,
                            }
                        )
                except Exception as e:
                    self.stderr.write(f'Failed to upsert subscription {sub_id}: {e}')

                # Payment: inspect latest_invoice/payment_intent
                try:
                    invoice = None
                    raw_invoice = s.get('latest_invoice') if isinstance(s, dict) else getattr(s, 'latest_invoice', None)
                    if raw_invoice:
                        if isinstance(raw_invoice, str):
                            invoice = stripe.Invoice.retrieve(raw_invoice, expand=['payment_intent', 'payment_intent.charges'])
                        elif isinstance(raw_invoice, dict):
                            if not raw_invoice.get('payment_intent') and raw_invoice.get('id'):
                                invoice = stripe.Invoice.retrieve(raw_invoice.get('id'), expand=['payment_intent', 'payment_intent.charges'])
                            else:
                                invoice = raw_invoice
                        else:
                            invoice = raw_invoice

                    if invoice:
                        invoice_id = invoice.get('id') if isinstance(invoice, dict) else getattr(invoice, 'id', None)
                        amount_paid = invoice.get('amount_paid') if isinstance(invoice, dict) else getattr(invoice, 'amount_paid', None)
                        currency = (invoice.get('currency') if isinstance(invoice, dict) else getattr(invoice, 'currency', None) or '').upper()
                        raw_pi = invoice.get('payment_intent') if isinstance(invoice, dict) else getattr(invoice, 'payment_intent', None)
                        payment_intent = None
                        if raw_pi:
                            if isinstance(raw_pi, str):
                                payment_intent = stripe.PaymentIntent.retrieve(raw_pi, expand=['charges'])
                            else:
                                payment_intent = raw_pi

                        charge_id = None
                        pi_status = ''
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

                        # avoid duplicates: check by payment_intent id or charge id
                        existing = None
                        pi_id = (getattr(payment_intent, 'id', None) if payment_intent else None) or (payment_intent.get('id') if isinstance(payment_intent, dict) else None)
                        if pi_id:
                            existing = SubscriptionPayment.objects.filter(stripe_payment_intent_id=pi_id).first()
                        if not existing and charge_id:
                            existing = SubscriptionPayment.objects.filter(stripe_charge_id=charge_id).first()

                        if not existing:
                            try:
                                amt = (int(amount_paid) / 100.0) if isinstance(amount_paid, (int, str)) and str(amount_paid).isdigit() else (float(amount_paid) if amount_paid else 0)
                            except Exception:
                                amt = 0
                        if dry:
                            self.stdout.write(f'[DRY] Would create payment record for subscription {sub_id}: amt={amt} {currency} invoice={invoice_id} pi={pi_id} charge={charge_id}')
                        else:
                            SubscriptionPayment.objects.create(
                                subscription=UserSubscription.objects.filter(stripe_subscription_id=sub_id).first(),
                                user=user,
                                amount=amt,
                                currency=(currency or '').upper(),
                                stripe_invoice_id=invoice_id,
                                stripe_payment_intent_id=pi_id,
                                stripe_charge_id=charge_id,
                                status=pi_status or ''
                            )
                except Exception as e:
                    self.stderr.write(f'Failed to sync payment for subscription {sub_id}: {e}')

            except Exception as e:
                self.stderr.write(f'Error processing subscription record: {e}')

        self.stdout.write(f'Synced {count} subscriptions from Stripe')
