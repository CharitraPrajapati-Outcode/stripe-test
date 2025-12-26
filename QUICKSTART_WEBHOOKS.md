# 🎯 Connected Account Invoice Webhook - Quick Start

## What Was Done

✅ Added webhook handling for Stripe Connect invoice payments  
✅ Auto-updates `ConnectedAccountInvoice.status` when customers pay  
✅ Added status tracking: `pending` → `paid` / `payment_failed`  
✅ Created testing tools to verify webhook functionality  

## How It Works

1. **You send an invoice** → Status: `pending`
2. **Customer pays invoice** → Stripe sends webhook event
3. **Webhook updates DB** → Status: `paid` + timestamp
4. **You're notified** → Invoice record reflects payment status

## Quick Setup (3 Steps)

### 1. Configure Webhook in Stripe Dashboard

Go to: https://dashboard.stripe.com/webhooks

Add these **Connect** events:
- ✅ `invoice.paid`
- ✅ `invoice.payment_succeeded`  
- ✅ `invoice.payment_failed`

Endpoint URL:
```
https://your-domain.com/stripe/webhook/
```

### 2. Add Webhook Secret(s) to .env

**Option A: One webhook endpoint** (simpler)
```bash
STRIPE_WEBHOOK_SECRET=whsec_your_secret_from_stripe
STRIPE_CONNECT_WEBHOOK_SECRET=whsec_your_secret_from_stripe
```

**Option B: Two separate endpoints** (more organized)
```bash
STRIPE_WEBHOOK_SECRET=whsec_platform_secret
STRIPE_CONNECT_WEBHOOK_SECRET=whsec_connect_secret
```

The code will try both secrets automatically!

### 3. Test It!

```bash
# Check your setup
python manage.py check_webhooks

# Send a test invoice (via your app UI at /accounts/invoices/)
# Pay it with test card: 4242 4242 4242 4242
# Run check again
python manage.py check_webhooks
```

## For Local Testing with ngrok

```bash
# Terminal 1: Start your Django server
python manage.py runserver

# Terminal 2: Start ngrok
ngrok http 8000

# Use the ngrok HTTPS URL in Stripe webhook settings
# Example: https://abc123.ngrok.io/stripe/webhook/
```

## Or Use Stripe CLI (Easier!)

```bash
# Login and forward webhooks to local server
stripe listen --forward-to localhost:8000/stripe/webhook/

# Copy the webhook signing secret it outputs
# Add to .env: STRIPE_WEBHOOK_SECRET=whsec_...
```

## Checking Invoice Status

### In Django Shell
```python
from billing.models import ConnectedAccountInvoice

# See all invoices
ConnectedAccountInvoice.objects.all().values('stripe_invoice_id', 'status', 'paid_at')

# See paid invoices
ConnectedAccountInvoice.objects.filter(status='paid')
```

### Using Management Command
```bash
python manage.py check_webhooks
```

### In Your App
Visit: `/accounts/invoices/` to see invoice list

## Status Values

- `pending` - Invoice created, awaiting payment
- `paid` - Payment successful! ✅
- `payment_failed` - Payment attempt failed
- `void` - Invoice was voided/cancelled
- `error` - Error during creation

## Troubleshooting

**Webhook not working?**
- Check Stripe Dashboard → Webhooks → Logs
- Verify `STRIPE_WEBHOOK_SECRET` is correct
- Ensure webhook endpoint is publicly accessible (use ngrok for local dev)

**Still showing pending after payment?**
- Check Django logs for errors
- Verify connected account ID matches
- Ensure invoice ID in DB matches Stripe invoice ID

**Where are the logs?**
```bash
# Check application logs
tail -f logs/django.log

# Look for lines like:
# "Stripe Connect webhook from account acct_XXX"
# "Updated ConnectedAccountInvoice to paid status"
```

## What Events Are Handled

| Event | Effect |
|-------|--------|
| `invoice.paid` | Sets status to `paid`, records `paid_at` timestamp |
| `invoice.payment_succeeded` | Same as above (alternative event) |
| `invoice.payment_failed` | Sets status to `payment_failed` |

## Files Modified

- ✏️ `billing/views.py` - Added `handle_connect_invoice_payment_event()`
- ✏️ `billing/views.py` - Updated `stripe_webhook()` to route Connect events
- 📄 `billing/management/commands/check_webhooks.py` - Testing tool
- 📄 `WEBHOOK_SETUP.md` - Detailed documentation

## Need More Help?

See: [WEBHOOK_SETUP.md](WEBHOOK_SETUP.md) for detailed documentation

---

**Ready to test?** Run: `python manage.py check_webhooks`
