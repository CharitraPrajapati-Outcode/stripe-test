"""
Test script to verify webhook is receiving and processing connected account invoice events.

Run this after setting up webhooks to check if everything is working.
"""
from billing.models import ConnectedAccountInvoice
from django.contrib.auth import get_user_model

User = get_user_model()

def check_webhook_status():
    """Check the status of ConnectedAccountInvoices to verify webhook processing."""
    
    print("\n" + "="*70)
    print("CONNECTED ACCOUNT INVOICE STATUS CHECK")
    print("="*70 + "\n")
    
    invoices = ConnectedAccountInvoice.objects.all().order_by('-created_at')
    
    if not invoices.exists():
        print("❌ No invoices found in database")
        print("\nCreate an invoice first by:")
        print("1. Going to /accounts/invoices/")
        print("2. Filling out the form and sending an invoice")
        return
    
    print(f"Found {invoices.count()} invoice(s):\n")
    
    for inv in invoices:
        print(f"Invoice ID: {inv.stripe_invoice_id or '(not created in Stripe yet)'}")
        print(f"  Status: {inv.status}")
        print(f"  Amount: {inv.amount} {inv.currency}")
        print(f"  Email: {inv.customer_email}")
        print(f"  Connected Account: {inv.connected_account}")
        
        if inv.status == 'paid':
            print(f"  ✅ Paid at: {inv.paid_at}")
            print("  → Webhook is working! Invoice marked as paid.")
        elif inv.status == 'pending':
            print("  ⏳ Status: Pending payment")
            print(f"  → Payment link: {inv.hosted_invoice_url}")
            print("  → Pay this invoice to test webhook")
        elif inv.status == 'payment_failed':
            print("  ❌ Payment failed")
            print("  → Webhook received payment failure event")
        else:
            print(f"  ℹ️  Status: {inv.status}")
        
        print(f"  Created: {inv.created_at}")
        print()
    
    # Check for webhooks that might have been processed
    paid_count = invoices.filter(status='paid').count()
    pending_count = invoices.filter(status='pending').count()
    failed_count = invoices.filter(status='payment_failed').count()
    
    print("\n" + "-"*70)
    print("SUMMARY:")
    print(f"  Paid: {paid_count}")
    print(f"  Pending: {pending_count}")
    print(f"  Failed: {failed_count}")
    print("-"*70 + "\n")
    
    if paid_count > 0:
        print("✅ SUCCESS! You have paid invoices, which means webhooks are working!")
    elif pending_count > 0:
        print("⏳ You have pending invoices. Pay one to test webhook functionality.")
        print("\nTo test:")
        print("1. Copy the payment link from above")
        print("2. Open it in a browser")
        print("3. Use test card: 4242 4242 4242 4242")
        print("4. Complete the payment")
        print("5. Run this script again to see if status changed to 'paid'")
    
    return invoices

def check_webhook_config():
    """Check if webhook configuration looks correct."""
    
    print("\n" + "="*70)
    print("WEBHOOK CONFIGURATION CHECK")
    print("="*70 + "\n")
    
    from django.conf import settings
    
    # Check if webhook secret is configured
    webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', None)
    
    if webhook_secret:
        print("✅ STRIPE_WEBHOOK_SECRET is configured")
        print(f"   Value starts with: {webhook_secret[:10]}...")
    else:
        print("❌ STRIPE_WEBHOOK_SECRET is NOT configured")
        print("   Add it to your .env file:")
        print("   STRIPE_WEBHOOK_SECRET=whsec_your_secret_here")
    
    # Check Stripe API key
    stripe_key = getattr(settings, 'STRIPE_SECRET_KEY', None)
    if stripe_key:
        print("✅ STRIPE_SECRET_KEY is configured")
    else:
        print("❌ STRIPE_SECRET_KEY is NOT configured")
    
    print("\n" + "-"*70)
    print("WEBHOOK ENDPOINT:")
    print("  URL: /stripe/webhook/")
    print("  Handler: billing.views.stripe_webhook")
    print("  CSRF: Exempt (required for webhooks)")
    print("-"*70 + "\n")


if __name__ == '__main__':
    check_webhook_config()
    check_webhook_status()
