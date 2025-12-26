# Stripe Webhook Setup for Connected Account Invoices

## Overview
Your webhook handler has been updated to process invoice payment events for both:
1. **Platform invoices** (regular subscriptions)
2. **Connected account invoices** (invoices you send on behalf of connected accounts)

## What Was Added

The code now handles these Stripe Connect events:
- `invoice.paid` - When an invoice is successfully paid
- `invoice.payment_succeeded` - Alternative event for successful payment
- `invoice.payment_failed` - When payment fails

When these events are received from a connected account, your `ConnectedAccountInvoice` database records will be automatically updated with the correct status.

## Stripe Dashboard Setup

### Step 1: Configure Webhook Endpoint

1. Go to your Stripe Dashboard: https://dashboard.stripe.com/
2. Navigate to **Developers** → **Webhooks**
3. Click **Add endpoint** or use your existing webhook endpoint

### Step 2: Webhook URL

Your webhook URL is:
```
https://your-domain.com/stripe/webhook/
```

For local development with ngrok:
```
https://YOUR_NGROK_URL.ngrok.io/stripe/webhook/
```

### Step 3: Select Events to Listen To

You need to select these events:

#### For Platform Events (existing):
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.created`
- `invoice.payment_succeeded`
- `invoice.payment_failed`
- `payment_intent.succeeded`

#### For Connect Events (NEW - required for connected account invoices):
- `invoice.paid`
- `invoice.payment_succeeded`
- `invoice.payment_failed`

**Important:** Make sure to check "Connect" checkbox when selecting these invoice events, or they won't trigger for connected accounts!

### Step 4: Connect Account Configuration

Since you're using Stripe Connect, you need to ensure:

1. The webhook endpoint receives events from **both** your platform account **and** connected accounts
2. In webhook settings, ensure "Listen to events on Connected accounts" is enabled

### Step 5: Get Your Webhook Signing Secret

#### Option A: Separate Endpoints (Recommended)

If you create **two separate webhook endpoints** in Stripe (one for platform, one for Connect):

1. **Platform Webhook** - For subscription events
   - Copy its signing secret
   - Add to `.env`: `STRIPE_WEBHOOK_SECRET=whsec_your_platform_secret`

2. **Connect Webhook** - For connected account invoice events  
   - Copy its signing secret
   - Add to `.env`: `STRIPE_CONNECT_WEBHOOK_SECRET=whsec_your_connect_secret`

#### Option B: Single Endpoint (Simpler)

If you use **one webhook endpoint** for all events:

1. Copy the signing secret
2. Add it to **both** variables in `.env`:
   ```
   STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret
   STRIPE_CONNECT_WEBHOOK_SECRET=whsec_your_webhook_secret
   ```

#### How the Code Works

The webhook handler will:
1. Try to verify with `STRIPE_CONNECT_WEBHOOK_SECRET` first
2. If that fails, try `STRIPE_WEBHOOK_SECRET`
3. If both fail, reject the webhook

This flexible approach works whether you have:
- One endpoint with one secret (use same value for both)
- Two endpoints with different secrets (use different values)

## Testing the Webhook

### Option 1: Using Stripe CLI (Recommended for local development)

```bash
# Login to Stripe CLI
stripe login

# Forward webhooks to your local server
stripe listen --forward-to localhost:8000/stripe/webhook/

# This will output a webhook signing secret like: whsec_...
# Use this for STRIPE_WEBHOOK_SECRET in your .env
```

### Option 2: Using ngrok (if not using Stripe CLI)

```bash
# Start ngrok
ngrok http 8000

# Use the HTTPS URL in Stripe Dashboard webhook settings
# Example: https://abc123.ngrok.io/stripe/webhook/
```

### Option 3: Test a Payment

1. Create and send an invoice through your app (POST to `/accounts/invoices/`)
2. The invoice will be created with `status='pending'` in your database
3. Pay the invoice using the hosted invoice URL
4. Stripe will send a webhook event
5. Your handler will update the status to `'paid'` and set `paid_at` timestamp

## Monitoring Webhooks

### View Webhook Logs
1. Go to **Developers** → **Webhooks** in Stripe Dashboard
2. Click on your webhook endpoint
3. View **Logs** tab to see all events and responses

### Check Your Application Logs
```bash
# Your Django logs will show:
tail -f logs/django.log

# Look for lines like:
# INFO: Stripe Connect webhook from account acct_XXX: invoice.paid
# INFO: Updated ConnectedAccountInvoice inv_XXX to paid status
```

## Database Status Flow

Your `ConnectedAccountInvoice` status will follow this flow:

```
'pending'  →  'paid'  (when payment succeeds)
           →  'payment_failed'  (when payment fails)
           →  'void'  (if invoice is voided)
```

## Troubleshooting

### Webhook not receiving events?
- Check that your URL is publicly accessible
- Verify CSRF exemption is working (`@csrf_exempt` decorator)
- Check Stripe Dashboard webhook logs for delivery attempts
- Ensure `STRIPE_WEBHOOK_SECRET` is correctly set

### Events not being processed?
- Check your application logs for errors
- Verify the connected account ID matches (`event.account` field)
- Ensure the invoice ID exists in your database

### Still showing as 'pending' after payment?
- Check if the webhook event was received (Stripe Dashboard logs)
- Verify the `stripe_invoice_id` in your database matches the Stripe invoice ID
- Check application logs for any processing errors

## Security Notes

1. **Always verify webhook signatures** - The code uses `stripe.Webhook.construct_event()` which validates the signature
2. **Use HTTPS in production** - Never use HTTP for webhook endpoints
3. **Keep signing secret secure** - Never commit `STRIPE_WEBHOOK_SECRET` to version control

## Next Steps

After setting up:
1. Test with a real invoice payment
2. Check database to confirm status changes to 'paid'
3. Monitor logs for any errors
4. Consider adding email notifications when invoices are paid

## Code Reference

The webhook handler is in: `billing/views.py`
- Main handler: `stripe_webhook()`
- Connect invoice handler: `handle_connect_invoice_payment_event()`
- URL pattern: `config/urls.py` → `stripe/webhook/`
