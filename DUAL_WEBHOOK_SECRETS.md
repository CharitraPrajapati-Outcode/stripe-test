# Dual Webhook Secret Configuration ✅

## What Was Updated

Your webhook handler now supports **both** webhook secrets and will intelligently try both to verify incoming webhook events.

## Configuration in .env

You now have two webhook secret variables:

```bash
STRIPE_WEBHOOK_SECRET=whsec_your_secret
STRIPE_CONNECT_WEBHOOK_SECRET=whsec_your_connect_secret
```

## How It Works

```
┌─────────────────────────────────────────────────┐
│         Webhook Event Arrives                    │
└────────────────┬────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────┐
│  1. Try STRIPE_CONNECT_WEBHOOK_SECRET           │
│     ├─ Success? ✓ Process event                 │
│     └─ Failed? Try next...                       │
└────────────────┬────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────┐
│  2. Try STRIPE_WEBHOOK_SECRET                   │
│     ├─ Success? ✓ Process event                 │
│     └─ Failed? ✗ Reject webhook (403)           │
└─────────────────────────────────────────────────┘
```

## Why This Approach?

### Flexibility
- ✅ Works with one endpoint or two separate endpoints
- ✅ Automatically tries both secrets
- ✅ Prioritizes Connect secret for Connect events

### Security
- ✅ Always verifies signatures
- ✅ Rejects webhooks if neither secret works
- ✅ Logs which secret was used

### Simplicity
- ✅ No need to modify code when adding/removing endpoints
- ✅ Same code works for all scenarios

## Usage Scenarios

### Scenario 1: One Webhook Endpoint (What You Have)

**Setup:**
- One endpoint in Stripe: `https://your-domain/stripe/webhook/`
- Listens to both platform and Connect events
- One signing secret from Stripe

**Configuration:**
```bash
# Use same secret for both (or just set one)
STRIPE_WEBHOOK_SECRET=whsec_your_secret
STRIPE_CONNECT_WEBHOOK_SECRET=whsec_your_secret
```

### Scenario 2: Two Separate Endpoints (More Organized)

**Setup:**
- Platform endpoint: `https://your-domain/stripe/webhook/`
  - Events: subscriptions, platform invoices
- Connect endpoint: `https://your-domain/stripe/webhook/`
  - Events: connected account invoices

**Configuration:**
```bash
# Different secrets for different endpoints
STRIPE_WEBHOOK_SECRET=whsec_platform_secret
STRIPE_CONNECT_WEBHOOK_SECRET=whsec_connect_secret
```

### Scenario 3: Only One Secret (Fallback)

**Setup:**
- Only have one of the secrets configured

**Configuration:**
```bash
# Handler will use whichever is available
STRIPE_WEBHOOK_SECRET=whsec_your_secret
# STRIPE_CONNECT_WEBHOOK_SECRET not set (optional)
```

## Code Changes Made

### 1. Settings ([config/settings.py](config/settings.py))
```python
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET')
STRIPE_CONNECT_WEBHOOK_SECRET = config('STRIPE_CONNECT_WEBHOOK_SECRET', default=None)
```

### 2. Webhook Handler ([billing/views.py](billing/views.py))
```python
# Get both secrets
webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', None)
connect_webhook_secret = getattr(settings, 'STRIPE_CONNECT_WEBHOOK_SECRET', None)

# Try Connect secret first, then platform secret
for secret_name, secret in secrets_to_try:
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        logger.debug('Successfully verified with %s secret', secret_name)
        break
    except:
        continue
```

### 3. Check Command ([billing/management/commands/check_webhooks.py](billing/management/commands/check_webhooks.py))
```python
# Now checks both secrets
if webhook_secret:
    print("✅ STRIPE_WEBHOOK_SECRET is configured")
if connect_webhook_secret:
    print("✅ STRIPE_CONNECT_WEBHOOK_SECRET is configured")
```

## Testing

Run this to verify configuration:
```bash
python manage.py check_webhooks
```

Expected output:
```
✅ STRIPE_WEBHOOK_SECRET is configured
✅ STRIPE_CONNECT_WEBHOOK_SECRET is configured
✅ STRIPE_SECRET_KEY is configured
```

## Logs to Look For

When webhook arrives, you'll see:
```
DEBUG: Successfully verified webhook signature with Connect secret
INFO: Stripe Connect webhook from account acct_XXX: invoice.paid
INFO: Updated ConnectedAccountInvoice inv_XXX to paid status
```

Or:
```
DEBUG: Signature verification failed with Connect secret: <error>
DEBUG: Successfully verified webhook signature with Platform secret
INFO: Stripe webhook: customer.subscription.updated
```

## Security Notes

✅ **Always verifies signatures** - Never processes unverified webhooks  
✅ **Tries multiple secrets safely** - No security compromise  
✅ **Logs verification attempts** - Easy debugging  
✅ **Rejects invalid signatures** - Returns 403 if all secrets fail  

## Your Current Setup is Ready! ✅

Since you already have both secrets in your `.env`:
- ✅ `STRIPE_WEBHOOK_SECRET` configured
- ✅ `STRIPE_CONNECT_WEBHOOK_SECRET` configured

Your webhook will now:
1. Receive events from Stripe
2. Try verifying with Connect secret first
3. Fall back to platform secret if needed
4. Process the event and update your database
5. Invoice status will change from `pending` → `paid` automatically!

---

**Ready to test?** 

Send an invoice → Customer pays → Check database status changes! 🎉
