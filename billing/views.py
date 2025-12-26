import json
import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import datetime
from decimal import Decimal
import logging

from django.contrib.auth import get_user_model
from billing.models import UserSubscription, SubscriptionPlan, SubscriptionPayment, ConnectedAccountInvoice
from billing.stripe_utils import StripeManager
from django.contrib.auth.models import Group

User = get_user_model()

logger = logging.getLogger(__name__)


def plan_name_to_role(plan_name: str) -> str:
    """Map a given plan name to one of the role candidates (lower-case).

    Uses substring matching so names like 'Athlete Monthly' -> 'athlete'.
    Any unknown or empty name returns 'free'.
    """
    if not plan_name:
        return 'free'
    n = plan_name.strip().lower()
    if 'athlete' in n:
        return 'athlete'
    if 'host' in n:
        return 'host'
    if 'guest' in n:
        return 'guest'
    if 'free' in n:
        return 'free'
    return 'free'


# ==================== Event Handler Functions ====================

def handle_subscription_created_or_updated(data):
    """Handle customer.subscription.created and customer.subscription.updated events."""
    stripe_mgr = StripeManager()
    sub = data
    sub_id = sub.get('id')
    customer = sub.get('customer')
    status = sub.get('status')
    cancel_at_period_end = sub.get('cancel_at_period_end')
    canceled_at = sub.get('canceled_at')

    logger.debug('=== SUBSCRIPTION DATA ===')
    logger.debug('Subscription ID: %s', sub_id)
    logger.debug('Customer: %s', customer)
    logger.debug('Status: %s', status)
    logger.debug('Full subscription object: %s', json.dumps(sub, default=str, indent=2)[:2000])  # First 2000 chars
    logger.debug('=== END SUBSCRIPTION DATA ===')

    logger.debug('Processing subscription %s: status=%s', sub_id, status)

    # Find local user
    user = User.objects.filter(stripe_customer_id=customer).first() if customer else None
    if not user:
        logger.warning('Could not find user for customer %s', customer)

    # Attempt to resolve plan from items
    plan_obj = None
    try:
        items = sub.get('items', {}).get('data') if isinstance(sub.get('items'), dict) else sub.get('items')
        if items and isinstance(items, list) and len(items) > 0:
            first = items[0]
            price = first.get('price') if isinstance(first, dict) else None
            price_id = price.get('id') if isinstance(price, dict) else (getattr(price, 'id', None) if price else None)
            if price_id:
                plan_obj = SubscriptionPlan.objects.filter(stripe_price_id=price_id).first()
                if plan_obj:
                    logger.debug('Found plan %s for price %s', plan_obj.id, price_id)
    except Exception as e:
        logger.exception('Error resolving plan from subscription items')
        plan_obj = None

    # Extract period dates from webhook data using StripeManager
    extracted_data = stripe_mgr.extract_subscription_data(sub)
    cps = extracted_data['current_period_start']
    cpe = extracted_data['current_period_end']

    # Update or create local subscription
    usub, created = UserSubscription.objects.update_or_create(
        stripe_subscription_id=sub_id,
        defaults={
            'user': user,
            'plan': plan_obj,
            'status': status or '',
            'current_period_start': cps,
            'current_period_end': cpe,
            'cancel_at_period_end': bool(cancel_at_period_end) if cancel_at_period_end is not None else False,
            'cancelled_at': (stripe_mgr._to_datetime(canceled_at) if canceled_at else None),
        }
    )
    logger.info('Subscription %s %s: user=%s, plan=%s, status=%s, cps=%s, cpe=%s', sub_id, 'created' if created else 'updated', user, plan_obj, status, cps, cpe)
    # Assign role/group to the user based on the plan name, but respect scheduled cancellations
    def plan_name_to_role(plan_name: str) -> str:
        """Map a given plan name to one of the role candidates (lower-case).

        Uses substring matching so names like 'Athlete Monthly' -> 'athlete'.
        Any unknown or empty name returns 'free'.
        """
        if not plan_name:
            return 'free'
        n = plan_name.strip().lower()
        if 'athlete' in n:
            return 'athlete'
        if 'host' in n:
            return 'host'
        if 'guest' in n:
            return 'guest'
        if 'free' in n:
            return 'free'
        return 'free'

    try:
        if user:
            # If subscription is canceled (finalized), remove the role corresponding to this subscription
            if (status == 'canceled') or (usub.cancelled_at is not None):
                try:
                    role = plan_name_to_role(usub.plan.name if usub.plan else None)
                    g = Group.objects.filter(name=role).first()
                    if g and user.groups.filter(pk=g.pk).exists():
                        user.groups.remove(g)
                        logger.info('Removed role group "%s" from user %s due to cancellation', role, user)
                except Exception:
                    logger.exception('Error removing role group for user %s on cancellation', user)
            else:
                # If cancellation is scheduled at period end, keep existing groups until deletion
                if usub.cancel_at_period_end:
                    logger.debug('Subscription %s is scheduled to cancel at period end; preserving user groups until end date', sub_id)
                else:
                    # Add role for this subscription's plan (allow multiple groups per user)
                    try:
                        desired = plan_name_to_role(plan_obj.name if plan_obj else None)
                        g, _ = Group.objects.get_or_create(name=desired)
                        if not user.groups.filter(pk=g.pk).exists():
                            user.groups.add(g)
                            logger.info('Added role group "%s" to user %s', desired, user)
                    except Exception:
                        logger.exception('Error adding role group for user %s', user)
    except Exception:
        logger.exception('Unexpected error while handling role groups for user %s', user)


def handle_subscription_deleted(data):
    """Handle customer.subscription.deleted event."""
    sub = data
    sub_id = sub.get('id')
    canceled_at = sub.get('canceled_at')

    logger.debug('Processing subscription deletion: %s', sub_id)

    usub = UserSubscription.objects.filter(stripe_subscription_id=sub_id).first()
    if usub:
        usub.status = 'canceled'
        try:
            if canceled_at:
                usub.cancelled_at = datetime.fromtimestamp(int(canceled_at), tz=timezone.utc)
            else:
                usub.cancelled_at = timezone.now()
        except Exception:
            usub.cancelled_at = timezone.now()
        usub.save()
        logger.info('Subscription %s marked as canceled', sub_id)
        # When subscription deleted, remove only the role corresponding to this plan
        try:
            if usub.user:
                role = plan_name_to_role(usub.plan.name if usub.plan else None)
                g = Group.objects.filter(name=role).first()
                if g and usub.user.groups.filter(pk=g.pk).exists():
                    usub.user.groups.remove(g)
                    logger.info('Removed role group "%s" from user %s after subscription deletion', role, usub.user)

                # If user now has no role groups among candidates, add 'free'
                role_candidates = ['free', 'athlete', 'host', 'guest']
                has_role = False
                for r in role_candidates:
                    gg = Group.objects.filter(name=r).first()
                    if gg and usub.user.groups.filter(pk=gg.pk).exists():
                        has_role = True
                        break
                if not has_role:
                    free_group, _ = Group.objects.get_or_create(name='free')
                    usub.user.groups.add(free_group)
                    logger.info('Assigned user %s to role group "free" because no other role groups present', usub.user)
        except Exception:
            logger.exception('Error updating role groups after subscription deletion for user %s', usub.user)
    else:
        logger.warning('Could not find subscription for deletion: %s', sub_id)


def handle_invoice_created(data):
    """Handle invoice.created event - create pending SubscriptionPayment record."""
    stripe_mgr = StripeManager()
    
    inv = data
    invoice_id = inv.get('id')
    sub_id = inv.get('subscription')
    amount_due = inv.get('amount_due')
    currency = (inv.get('currency') or '').upper()
    
    logger.debug('Invoice created: id=%s, subscription=%s, amount_due=%s', invoice_id, sub_id, amount_due)
    
    # Find the subscription and user
    usub = UserSubscription.objects.filter(stripe_subscription_id=sub_id).first() if sub_id else None
    if not usub:
        logger.warning('Could not find subscription for invoice %s (sub_id=%s)', invoice_id, sub_id)
        return
    
    # Check if payment already recorded (idempotency)
    if SubscriptionPayment.objects.filter(stripe_invoice_id=invoice_id).exists():
        logger.debug('Payment already recorded for invoice %s, skipping', invoice_id)
        return
    
    try:
        # Convert amount to Decimal
        from decimal import Decimal
        amt = Decimal(str(int(amount_due) / 100.0)) if amount_due else Decimal('0.00')
        
        # Create payment record with 'pending' status
        # Attempt to determine authoritative invoice PDF/hosted URL
        invoice_pdf = None
        try:
            # Prefer invoice field if present in webhook payload
            invoice_pdf = inv.get('invoice_pdf') or inv.get('hosted_invoice_url')
            if not invoice_pdf:
                # Fetch full invoice from Stripe (safe fallback)
                logger.debug('Fetching full invoice from Stripe to obtain PDF/hosted URL for invoice %s', invoice_id)
                full_invoice = stripe.Invoice.retrieve(invoice_id)
                invoice_pdf = (full_invoice.get('invoice_pdf') if isinstance(full_invoice, dict) else getattr(full_invoice, 'invoice_pdf', None)) or (full_invoice.get('hosted_invoice_url') if isinstance(full_invoice, dict) else getattr(full_invoice, 'hosted_invoice_url', None))
        except Exception:
            invoice_pdf = None

        payment = SubscriptionPayment.objects.create(
            subscription=usub,
            user=usub.user,
            amount=amt,
            currency=currency,
            stripe_invoice_id=invoice_id,
            invoice_pdf_url=invoice_pdf,
            status='pending'  # Start with pending status
        )
        logger.info('Created pending payment for invoice %s: amount=%s, subscription=%s', invoice_id, amt, sub_id)
    except Exception as e:
        logger.exception('ERROR creating SubscriptionPayment for invoice %s: %s', invoice_id, str(e))


def handle_invoice_payment_event(data, event_type):
    """Handle invoice.payment_succeeded and invoice.payment_failed events."""
    stripe_mgr = StripeManager()
    
    inv = data
    invoice_id = inv.get('id')
    status = inv.get('status')
    
    # If invoice is still in draft, finalize it
    if status == 'draft':
        try:
            logger.debug('Invoice %s is in draft status, attempting to finalize...', invoice_id)
            stripe_mgr.finalize_invoice(invoice_id)
            logger.info('Successfully finalized draft invoice %s', invoice_id)
        except Exception as e:
            logger.warning('Could not finalize invoice %s: %s', invoice_id, str(e))
    
    # Try to get subscription from top level first, then from nested structure
    sub_id = inv.get('subscription')
    if not sub_id:
        # Try to extract from lines.data[0].parent.subscription_item_details.subscription
        try:
            lines = inv.get('lines', {}).get('data') if isinstance(inv.get('lines'), dict) else inv.get('lines')
            if lines and isinstance(lines, list) and len(lines) > 0:
                first_line = lines[0]
                parent = first_line.get('parent') if isinstance(first_line, dict) else getattr(first_line, 'parent', None)
                if parent:
                    if isinstance(parent, dict):
                        sub_id = parent.get('subscription_item_details', {}).get('subscription')
                    else:
                        sub_id = getattr(parent, 'subscription_item_details', {}).get('subscription')
        except Exception:
            pass
    
    amount_paid = inv.get('amount_paid')
    amount_due = inv.get('amount_due')
    logger.debug('Initial amount_paid from invoice: %s, amount_due: %s', amount_paid, amount_due)
    
    # Use amount_due (the actual charge amount), fallback to amount_paid
    amount = amount_due if amount_due is not None else amount_paid
    
    # Find subscription first (needed to use plan price)
    usub = UserSubscription.objects.filter(stripe_subscription_id=sub_id).first() if sub_id else None
    if not usub:
        logger.warning('Could not find subscription for invoice %s (sub_id=%s)', invoice_id, sub_id)
    
    # If amount is still 0, fetch the subscription plan price
    if (not amount or amount == 0) and usub and usub.plan:
        logger.debug('Amount is 0, using subscription plan price instead')
        plan_price = usub.plan.price
        if plan_price:
            # Convert Decimal price to cents (integer)
            amount = int(plan_price * 100)
            logger.debug('Using plan price: %s (in cents: %s)', plan_price, amount)
    
    # If amount is still 0, try to get from line items (for complex prorations)
    if not amount or amount == 0:
        try:
            lines = inv.get('lines', {}).get('data') if isinstance(inv.get('lines'), dict) else inv.get('lines')
            logger.debug('Amount is still 0, attempting to extract from line items, lines type: %s, count: %s', type(lines), len(lines) if lines else 0)
            if lines and isinstance(lines, list):
                # Try to get unit_amount_decimal or amount from price details
                for line in lines:
                    if isinstance(line, dict):
                        pricing = line.get('pricing', {})
                        price_details = pricing.get('price_details', {})
                        # Get unit_amount_decimal from price_details
                        unit_amount_decimal = price_details.get('unit_amount_decimal')
                        if unit_amount_decimal:
                            try:
                                amount = int(Decimal(unit_amount_decimal))
                                logger.debug('Extracted unit_amount_decimal from price_details: %s', amount)
                                break
                            except:
                                pass
                        # Fallback: get amount from line item
                        line_amount = line.get('amount', 0)
                        if line_amount:
                            amount = line_amount
                            logger.debug('Extracted amount from line item: %s', amount)
                            break
        except Exception as e:
            logger.exception('Error extracting amount from line items: %s', str(e))
    
    currency = (inv.get('currency') or '').upper()
    payment_intent = inv.get('payment_intent')
    
    # Log full invoice structure for debugging
    logger.debug('=== FULL INVOICE DATA ===')
    logger.debug('Invoice ID: %s', invoice_id)
    logger.debug('Subscription (from invoice): %s', sub_id)
    logger.debug('Amount Paid: %s', amount_paid)
    logger.debug('Payment Intent (raw): %s', payment_intent)
    logger.debug('Full invoice object (first 3000 chars): %s', json.dumps(inv, default=str, indent=2)[:3000])
    logger.debug('=== END INVOICE DATA ===')

    logger.debug('Processing invoice %s: sub_id=%s, amount=%s, status=%s', invoice_id, sub_id, amount_paid, event_type)

    # If subscription is not in webhook data, try to fetch full invoice from Stripe
    if not sub_id:
        try:
            logger.debug('Subscription ID missing from invoice webhook, fetching from Stripe: %s', invoice_id)
            full_invoice = stripe.Invoice.retrieve(invoice_id)
            sub_id = full_invoice.get('subscription') if isinstance(full_invoice, dict) else getattr(full_invoice, 'subscription', None)
            logger.debug('Retrieved subscription %s from Stripe for invoice %s', sub_id, invoice_id)
        except Exception as e:
            logger.warning('Could not fetch invoice from Stripe: %s', str(e))

    # Re-lookup usub if we fetched sub_id from Stripe
    if not usub and sub_id:
        usub = UserSubscription.objects.filter(stripe_subscription_id=sub_id).first()
        if not usub:
            logger.warning('Could not find subscription for invoice %s (sub_id=%s)', invoice_id, sub_id)

    # Update usersubscription current period start and end from Stripe (like refresh-subscriptions API)
    if usub and sub_id:
        try:
            logger.debug('Fetching subscription details from Stripe for %s', sub_id)
            remote = stripe_mgr.retrieve_subscription(sub_id)
            data_extracted = stripe_mgr.extract_subscription_data(remote)
            
            updated = False
            if data_extracted['current_period_start'] and data_extracted['current_period_start'] != usub.current_period_start:
                usub.current_period_start = data_extracted['current_period_start']
                updated = True
            if data_extracted['current_period_end'] and data_extracted['current_period_end'] != usub.current_period_end:
                usub.current_period_end = data_extracted['current_period_end']
                updated = True
            if data_extracted['status'] and data_extracted['status'] != usub.status:
                usub.status = data_extracted['status']
                updated = True
            
            if updated:
                usub.save()
                logger.info('Updated subscription %s period: cps=%s, cpe=%s, status=%s', 
                           sub_id, data_extracted['current_period_start'], data_extracted['current_period_end'], data_extracted['status'])
        except Exception as e:
            logger.warning('Could not update subscription periods for %s: %s', sub_id, str(e))

    # Record payment if we have a subscription (amount can be 0 for prorations or credits)
    logger.debug('Payment creation condition check: usub=%s, payment_intent=%s, amount=%s', bool(usub), bool(payment_intent), amount)
    
    # Check if payment already exists
    existing_payment = SubscriptionPayment.objects.filter(stripe_invoice_id=invoice_id).first()
    
    if event_type == 'invoice.payment_succeeded':
        if existing_payment:
            # Update existing payment to succeeded status
            logger.debug('Payment exists for invoice %s, updating status to succeeded', invoice_id)
            # Attempt to update invoice PDF/hosted URL if present in the invoice payload
            try:
                invoice_pdf = inv.get('invoice_pdf') or inv.get('hosted_invoice_url')
                if not invoice_pdf:
                    # fetch from stripe if missing
                    try:
                        full_invoice = stripe.Invoice.retrieve(invoice_id)
                        invoice_pdf = (full_invoice.get('invoice_pdf') if isinstance(full_invoice, dict) else getattr(full_invoice, 'invoice_pdf', None)) or (full_invoice.get('hosted_invoice_url') if isinstance(full_invoice, dict) else getattr(full_invoice, 'hosted_invoice_url', None))
                    except Exception:
                        invoice_pdf = None
                if invoice_pdf:
                    existing_payment.invoice_pdf_url = invoice_pdf
            except Exception:
                pass

            existing_payment.status = 'succeeded'
            existing_payment.save()
            logger.info('Updated payment status to succeeded for invoice %s', invoice_id)
            
            # Activate the subscription if it's in trialing status
            if usub and usub.status == 'trialing':
                usub.status = 'active'
                usub.save()
                logger.info('Activated subscription %s after payment succeeded', sub_id)
        else:
            # Create new payment record with succeeded status if we have a subscription
            logger.debug('Payment not previously recorded, creating new SubscriptionPayment with succeeded status...')
            if usub:  # Only need subscription, amount can be 0
                try:
                    amt = (int(amount) / 100.0) if amount is not None else 0
                    # Convert to Decimal for DecimalField
                    amt = Decimal(str(amt))
                except Exception as e:
                    logger.exception('Error converting amount to Decimal: %s', str(e))
                    amt = Decimal('0.00')
                
                pi_id = None
                charge_id = None

                # Extract payment_intent id from webhook data (can be string ID or object)
                logger.debug('Extracting payment_intent from invoice data...')
                if isinstance(payment_intent, dict):
                    pi_id = payment_intent.get('id')
                    logger.debug('Payment intent is dict, extracted id: %s', pi_id)
                elif isinstance(payment_intent, str):
                    pi_id = payment_intent
                    logger.debug('Payment intent is string id: %s', pi_id)
                else:
                    logger.debug('Payment intent is unexpected type: %s', type(payment_intent))

                # If we have a payment_intent ID, fetch full details to extract charge ID
                if pi_id:
                    try:
                        logger.debug('Fetching payment intent details from Stripe: %s', pi_id)
                        pi_obj = stripe_mgr.retrieve_payment_intent(pi_id)
                        logger.debug('Full PaymentIntent object: %s', json.dumps(pi_obj if isinstance(pi_obj, dict) else {'id': getattr(pi_obj, 'id', None), 'charges': str(getattr(pi_obj, 'charges', None))}, default=str))
                        pi_data = stripe_mgr.extract_payment_intent_data(pi_obj)
                        
                        logger.debug('Extracted PaymentIntent data: %s', json.dumps(pi_data, default=str))
                        
                        # Extract charge ID if available
                        if pi_data['charges'] and len(pi_data['charges']) > 0:
                            charge_id = pi_data['charges'][0]
                            logger.debug('Extracted charge ID %s from payment intent %s', charge_id, pi_id)
                        else:
                            logger.debug('No charges found in payment intent %s', pi_id)
                    except Exception as e:
                        logger.exception('Could not fetch payment intent %s: %s', pi_id, str(e))

                logger.debug('Creating payment with: pi_id=%s, charge_id=%s, amount=%s', pi_id, charge_id, amt)
                
                try:
                    payment = SubscriptionPayment.objects.create(
                        subscription=usub,
                        user=usub.user if usub else None,
                        amount=amt,
                        currency=currency,
                        stripe_invoice_id=invoice_id,
                        invoice_pdf_url=(inv.get('invoice_pdf') or inv.get('hosted_invoice_url')),
                        stripe_payment_intent_id=pi_id,
                        stripe_charge_id=charge_id,
                        status='succeeded'
                    )
                    logger.info('Created succeeded payment for invoice %s: amount=%s, subscription=%s, pi=%s, charge=%s', 
                               invoice_id, amt, sub_id, pi_id, charge_id)
                    
                    # Activate the subscription if it's in trialing status
                    if usub and usub.status == 'trialing':
                        usub.status = 'active'
                        usub.save()
                        logger.info('Activated subscription %s after payment succeeded', sub_id)
                except Exception as e:
                    logger.exception('ERROR creating SubscriptionPayment for invoice %s: %s', invoice_id, str(e))
            else:
                logger.debug('No subscription found, skipping payment record creation')
    
    elif event_type == 'invoice.payment_failed':
        if existing_payment:
            # Update existing payment to failed status and update invoice PDF url if available
            logger.debug('Payment exists for invoice %s, updating status to failed', invoice_id)
            try:
                invoice_pdf = inv.get('invoice_pdf') or inv.get('hosted_invoice_url')
                if not invoice_pdf:
                    try:
                        full_invoice = stripe.Invoice.retrieve(invoice_id)
                        invoice_pdf = (full_invoice.get('invoice_pdf') if isinstance(full_invoice, dict) else getattr(full_invoice, 'invoice_pdf', None)) or (full_invoice.get('hosted_invoice_url') if isinstance(full_invoice, dict) else getattr(full_invoice, 'hosted_invoice_url', None))
                    except Exception:
                        invoice_pdf = None
                if invoice_pdf:
                    existing_payment.invoice_pdf_url = invoice_pdf
            except Exception:
                pass

            existing_payment.status = 'failed'
            existing_payment.save()
            logger.info('Updated payment status to failed for invoice %s', invoice_id)
        else:
            # Create new payment record with failed status
            logger.debug('Payment not previously recorded, creating new SubscriptionPayment with failed status...')
            if usub:
                try:
                    amt = (int(amount) / 100.0) if amount is not None else 0
                    amt = Decimal(str(amt))
                except Exception as e:
                    logger.exception('Error converting amount to Decimal: %s', str(e))
                    amt = Decimal('0.00')

                try:
                    payment = SubscriptionPayment.objects.create(
                        subscription=usub,
                        user=usub.user if usub else None,
                        amount=amt,
                        currency=currency,
                        stripe_invoice_id=invoice_id,
                        invoice_pdf_url=(inv.get('invoice_pdf') or inv.get('hosted_invoice_url')),
                        status='failed'
                    )
                    logger.info('Created failed payment for invoice %s: amount=%s, subscription=%s', invoice_id, amt, sub_id)
                except Exception as e:
                    logger.exception('ERROR creating failed SubscriptionPayment for invoice %s: %s', invoice_id, str(e))


def handle_payment_intent_succeeded(data):
    """Handle payment_intent.succeeded event (optional safety catch)."""
    pi = data
    pi_id = pi.get('id')
    logger.debug('Payment intent succeeded: %s', pi_id)
    # Could be extended to handle additional logic if needed


def handle_connect_invoice_payment_event(data, event_type, account_id):
    """Handle invoice payment events for Stripe Connect accounts.
    
    Updates ConnectedAccountInvoice status when invoice is paid or payment fails.
    These events come with stripe_account header from connected accounts.
    """
    inv = data
    invoice_id = inv.get('id')
    status = inv.get('status')
    amount_paid = inv.get('amount_paid')
    paid = inv.get('paid', False)
    
    logger.info('Processing Connect invoice event %s for account %s: invoice=%s, status=%s, paid=%s', 
                event_type, account_id, invoice_id, status, paid)
    
    # Find the ConnectedAccountInvoice record
    invoice_record = ConnectedAccountInvoice.objects.filter(
        stripe_invoice_id=invoice_id,
        connected_account=account_id
    ).first()
    
    if not invoice_record:
        logger.warning('No ConnectedAccountInvoice found for invoice %s on account %s', invoice_id, account_id)
        return
    
    # Update invoice status based on event
    if event_type == 'invoice.paid' or (event_type == 'invoice.payment_succeeded' and paid):
        invoice_record.status = 'paid'
        invoice_record.paid_at = timezone.now()
        
        # Update invoice PDF URL if available
        invoice_pdf = inv.get('invoice_pdf') or inv.get('hosted_invoice_url')
        if invoice_pdf and not invoice_record.invoice_pdf_url:
            invoice_record.invoice_pdf_url = invoice_pdf
        
        invoice_record.save()
        logger.info('Updated ConnectedAccountInvoice %s to paid status', invoice_id)
        
    elif event_type == 'invoice.payment_failed':
        invoice_record.status = 'payment_failed'
        invoice_record.save()
        logger.info('Updated ConnectedAccountInvoice %s to payment_failed status', invoice_id)
        
    elif status == 'void':
        invoice_record.status = 'void'
        invoice_record.save()
        logger.info('Updated ConnectedAccountInvoice %s to void status', invoice_id)


# ==================== Main Webhook Handler ====================

@csrf_exempt
def stripe_webhook(request):
    """Main webhook handler that routes events to appropriate handlers."""
    payload = request.body
    logger.debug('Received stripe webhook payload: %d bytes', len(payload or b''))
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    
    # Get both webhook secrets
    webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', None)
    connect_webhook_secret = getattr(settings, 'STRIPE_CONNECT_WEBHOOK_SECRET', None)

    event = None
    
    # Try to verify with both secrets (try Connect secret first, then platform secret)
    secrets_to_try = []
    if connect_webhook_secret:
        secrets_to_try.append(('Connect', connect_webhook_secret))
    if webhook_secret:
        secrets_to_try.append(('Platform', webhook_secret))
    
    if not secrets_to_try:
        # If no webhook secrets configured, fall back to naive parsing (not recommended for production)
        logger.warning('No webhook secrets configured, using naive parsing')
        try:
            event = stripe.Event.construct_from(json.loads(payload.decode('utf-8')), stripe.api_key)
        except ValueError:
            logger.exception('Invalid payload when parsing stripe webhook')
            return HttpResponseBadRequest('Invalid payload')
    else:
        # Try each secret until one works
        last_error = None
        for secret_name, secret in secrets_to_try:
            try:
                event = stripe.Webhook.construct_event(payload, sig_header, secret)
                logger.debug('Successfully verified webhook signature with %s secret', secret_name)
                break
            except stripe.error.SignatureVerificationError as e:
                logger.debug('Signature verification failed with %s secret: %s', secret_name, str(e))
                last_error = e
                continue
            except ValueError as e:
                logger.exception('Invalid payload when parsing stripe webhook')
                return HttpResponseBadRequest('Invalid payload')
            except Exception as e:
                logger.debug('Error verifying with %s secret: %s', secret_name, str(e))
                last_error = e
                continue
        
        if not event:
            logger.error('Failed to verify webhook signature with any configured secret')
            return HttpResponseForbidden('Invalid signature')

    typ = event['type']
    data = event.get('data', {}).get('object', {})
    
    # Check if this is a Connect event (has account field)
    account = event.get('account')
    is_connect_event = bool(account)

    try:
        if is_connect_event:
            logger.info('Stripe Connect webhook from account %s: %s', account, event.get('type'))
        else:
            logger.info('Stripe webhook: %s', event.get('type'))
        # logger.debug('Event data: %s', json.dumps(data, default=str))

        # Handle Connect account events separately
        if is_connect_event:
            # Route Connect events
            if typ in ['invoice.paid', 'invoice.payment_succeeded', 'invoice.payment_failed']:
                handle_connect_invoice_payment_event(data, typ, account)
            else:
                logger.debug('Unhandled Connect event type: %s', typ)
        else:
            # Route platform account events (existing handlers)
            if typ == 'customer.subscription.created' or typ == 'customer.subscription.updated':
                handle_subscription_created_or_updated(data)

            elif typ == 'customer.subscription.deleted':
                handle_subscription_deleted(data)

            elif typ == 'invoice.created':
                handle_invoice_created(data)

            elif typ == 'invoice.payment_succeeded' or typ == 'invoice.payment_failed':
                handle_invoice_payment_event(data, typ)

            elif typ == 'payment_intent.succeeded':
                handle_payment_intent_succeeded(data)

            else:
                logger.debug('Unhandled event type: %s', typ)

    except Exception:
        logger.exception('Error handling stripe webhook event: %s', typ)

    return HttpResponse(status=200)
