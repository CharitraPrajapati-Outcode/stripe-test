"""
Stripe utility class to encapsulate all Stripe API interactions.
Provides clean methods for customer, subscription, and payment operations.
"""
import stripe
from decimal import Decimal
from django.conf import settings
from datetime import datetime, timezone
import time


class StripeManager:
    """Centralized manager for Stripe API operations."""

    def __init__(self):
        """Initialize Stripe API key from settings."""
        api_key = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if api_key:
            stripe.api_key = api_key

    def _to_datetime(self, timestamp):
        """Convert Stripe Unix timestamp to timezone-aware datetime."""
        if timestamp is None or timestamp == '':
            return None
        try:
            # Handle both string and integer timestamps
            ts = int(timestamp) if isinstance(timestamp, str) else timestamp
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            return None

    # ==================== Customer Operations ====================

    def get_or_create_customer(self, user):
        """Get existing Stripe customer or create a new one."""
        if getattr(user, 'stripe_customer_id', None):
            return user.stripe_customer_id

        try:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.get_full_name() or user.username
            )
            user.stripe_customer_id = customer.id
            user.save()
            return customer.id
        except Exception as e:
            raise Exception(f'Failed to create Stripe customer: {str(e)}')

    def list_payment_methods(self, customer_id):
        """List all payment methods for a customer."""
        try:
            pm_list = stripe.PaymentMethod.list(customer=customer_id, type='card')
            return [
                {
                    'id': m.id,
                    'brand': m.card.brand,
                    'last4': m.card.last4,
                    'exp_month': m.card.exp_month,
                    'exp_year': m.card.exp_year
                }
                for m in pm_list.data
            ]
        except Exception:
            return []

    def attach_payment_method(self, payment_method_id, customer_id):
        """Attach a payment method to a customer."""
        try:
            stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
            return True
        except Exception:
            # Already attached or other error — ignore
            return False

    # ==================== Setup Intent Operations ====================

    def create_setup_intent(self, customer_id):
        """Create a SetupIntent for capturing payment method."""
        try:
            return stripe.SetupIntent.create(customer=customer_id)
        except Exception as e:
            raise Exception(f'Failed to create SetupIntent: {str(e)}')

    # ==================== Price Operations ====================

    def get_price(self, price_id):
        """Fetch price details from Stripe."""
        try:
            return stripe.Price.retrieve(price_id, expand=['product'])
        except Exception as e:
            raise Exception(f'Failed to fetch price {price_id}: {str(e)}')

    def list_prices(self):
        """List all active prices from Stripe."""
        try:
            prices = stripe.Price.list(active=True, limit=100)
            return prices.data
        except Exception:
            return []

    def get_price_amount(self, price):
        """Extract and format price amount from Stripe price object."""
        try:
            if getattr(price, 'unit_amount', None) is not None:
                return Decimal(int(price.unit_amount)) / Decimal(100)
            elif getattr(price, 'unit_amount_decimal', None) is not None:
                return Decimal(str(price.unit_amount_decimal))
            else:
                return Decimal('0.00')
        except Exception:
            return Decimal('0.00')

    # ==================== Subscription Operations ====================

    def create_subscription(self, customer_id, price_id, payment_method_id):
        """Create a new subscription for a customer."""
        try:
            # Attach payment method if needed
            self.attach_payment_method(payment_method_id, customer_id)

            # Create subscription with billing cycle anchor to ensure immediate invoicing
            # Use current time + 2 seconds to avoid "in the past" error
            billing_cycle_anchor = int(time.time()) + 2
            
            subscription = stripe.Subscription.create(
                customer=customer_id,
                items=[{'price': price_id}],
                default_payment_method=payment_method_id,
                collection_method='charge_automatically',  # Automatic billing
                billing_cycle_anchor=billing_cycle_anchor,  # Start billing cycle in future
                proration_behavior='create_prorations',  # Handle prorations
            )
            return subscription
        except Exception as e:
            raise Exception(f'Failed to create subscription: {str(e)}')

    def cancel_subscription(self, subscription_id, at_period_end=False):
        """Cancel a subscription immediately or at period end."""
        try:
            if at_period_end:
                # Schedule cancellation at period end
                return stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
            else:
                # Cancel immediately
                return stripe.Subscription.delete(subscription_id)
        except Exception as e:
            raise Exception(f'Failed to cancel subscription: {str(e)}')

    def retrieve_subscription(self, subscription_id):
        """Fetch subscription details from Stripe."""
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except Exception as e:
            raise Exception(f'Failed to retrieve subscription: {str(e)}')

    def finalize_invoice(self, invoice_id):
        """Finalize a draft invoice (sends it to be paid)."""
        try:
            return stripe.Invoice.finalize_invoice(invoice_id)
        except Exception as e:
            raise Exception(f'Failed to finalize invoice {invoice_id}: {str(e)}')

    def extract_subscription_data(self, subscription):
        """Extract key fields from a subscription object (handles dict or object)."""
        # Extract current_period_start and current_period_end from items.data[0] (nested)
        current_period_start = None
        current_period_end = None
        price_id = None
        
        try:
            # Handle dict vs object for items
            if isinstance(subscription, dict):
                items = subscription.get('items', {}).get('data')
            else:
                items_obj = getattr(subscription, 'items', None)
                items = getattr(items_obj, 'data', None) if items_obj else None
            
            # Extract period dates from first subscription item
            if items and isinstance(items, list) and len(items) > 0:
                first_item = items[0]
                if isinstance(first_item, dict):
                    current_period_start = first_item.get('current_period_start')
                    current_period_end = first_item.get('current_period_end')
                    # Also extract price_id from first item
                    price = first_item.get('price', {})
                    price_id = price.get('id') if isinstance(price, dict) else getattr(price, 'id', None)
                else:
                    current_period_start = getattr(first_item, 'current_period_start', None)
                    current_period_end = getattr(first_item, 'current_period_end', None)
                    # Also extract price_id from first item
                    price = getattr(first_item, 'price', None)
                    price_id = price.get('id') if isinstance(price, dict) else getattr(price, 'id', None)
        except Exception:
            pass

        data = {
            'id': subscription.get('id') if isinstance(subscription, dict) else getattr(subscription, 'id', None),
            'customer': subscription.get('customer') if isinstance(subscription, dict) else getattr(subscription, 'customer', None),
            'status': subscription.get('status') if isinstance(subscription, dict) else getattr(subscription, 'status', None),
            'current_period_start': self._to_datetime(current_period_start),
            'current_period_end': self._to_datetime(current_period_end),
            'cancel_at_period_end': subscription.get('cancel_at_period_end') if isinstance(subscription, dict) else getattr(subscription, 'cancel_at_period_end', False),
            'canceled_at': self._to_datetime(
                subscription.get('canceled_at') if isinstance(subscription, dict) else getattr(subscription, 'canceled_at', None)
            ),
            'price_id': price_id,
        }

        return data

    # ==================== Account Operations ====================

    def get_account_info(self):
        """Retrieve Stripe account information."""
        try:
            acct = stripe.Account.retrieve()
            bp = acct.get('business_profile') if isinstance(acct, dict) else getattr(acct, 'business_profile', None)
            if bp:
                name = bp.get('name') if isinstance(bp, dict) else getattr(bp, 'name', None)
                if name:
                    return name

            # Fallback
            name = acct.get('settings', {}).get('dashboard', {}).get('display_name') if isinstance(acct, dict) else getattr(acct, 'display_name', None)
            return name
        except Exception:
            return None

    # ==================== Invoice Operations ====================

    def retrieve_invoice(self, invoice_id):
        """Fetch invoice details from Stripe."""
        try:
            return stripe.Invoice.retrieve(invoice_id, expand=['payment_intent', 'payment_intent.charges'])
        except Exception as e:
            raise Exception(f'Failed to retrieve invoice: {str(e)}')

    # ==================== Connected Account (Stripe Connect) Helpers ====================

    def create_connected_account(self, country='US', account_type='express'):
        """Create a Stripe Connected Account (Express by default).

        Returns the created account object.
        """
        try:
            acct = stripe.Account.create(
                country=country,
                type=account_type,
                capabilities={
                    'card_payments': {'requested': True},
                    'transfers': {'requested': True},
                }
            )
            return acct
        except Exception as e:
            raise Exception(f'Failed to create connected account: {str(e)}')

    def create_account_link(self, account_id, refresh_url, return_url):
        """Create an Account Link for Express onboarding flow.

        `refresh_url` is where Stripe will redirect if the user cancels; `return_url`
        is where Stripe will send the user after onboarding completes.
        """
        try:
            # Use the AccountLink resource to create onboarding links
            link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type='account_onboarding'
            )
            return link
        except Exception as e:
            raise Exception(f'Failed to create account link: {str(e)}')

    def retrieve_account(self, account_id):
        """Retrieve a connected account's details from Stripe."""
        try:
            return stripe.Account.retrieve(account_id)
        except Exception as e:
            raise Exception(f'Failed to retrieve connected account: {str(e)}')

    def delete_connected_account(self, account_id):
        """Delete a connected account on Stripe.

        Note: Stripe may prevent deletion in some cases; callers should handle
        exceptions and present appropriate messages to users.
        """
        try:
            return stripe.Account.delete(account_id)
        except Exception as e:
            raise Exception(f'Failed to delete connected account: {str(e)}')

    def extract_invoice_data(self, invoice):
        """Extract key fields from invoice object."""
        return {
            'id': invoice.get('id') if isinstance(invoice, dict) else getattr(invoice, 'id', None),
            'subscription': invoice.get('subscription') if isinstance(invoice, dict) else getattr(invoice, 'subscription', None),
            'amount_paid': invoice.get('amount_paid') if isinstance(invoice, dict) else getattr(invoice, 'amount_paid', None),
            'currency': (invoice.get('currency') if isinstance(invoice, dict) else getattr(invoice, 'currency', None) or '').upper(),
            'payment_intent': invoice.get('payment_intent') if isinstance(invoice, dict) else getattr(invoice, 'payment_intent', None),
        }

    # ==================== Payment Intent Operations ====================

    def retrieve_payment_intent(self, payment_intent_id):
        """Fetch payment intent details from Stripe."""
        try:
            return stripe.PaymentIntent.retrieve(payment_intent_id, expand=['charges'])
        except Exception as e:
            raise Exception(f'Failed to retrieve payment intent: {str(e)}')

    def extract_payment_intent_data(self, payment_intent):
        """Extract key fields from payment intent object."""
        data = {
            'id': payment_intent.get('id') if isinstance(payment_intent, dict) else getattr(payment_intent, 'id', None),
            'status': payment_intent.get('status') if isinstance(payment_intent, dict) else getattr(payment_intent, 'status', None),
            'charges': [],
        }

        # Extract charge IDs
        try:
            if isinstance(payment_intent, dict):
                charges = payment_intent.get('charges', {}).get('data', [])
            else:
                charges = getattr(payment_intent.charges, 'data', []) if hasattr(payment_intent, 'charges') else []

            if charges:
                data['charges'] = [
                    c.get('id') if isinstance(c, dict) else getattr(c, 'id', None)
                    for c in charges
                ]
        except Exception:
            pass

        return data
