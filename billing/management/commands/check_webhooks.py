"""
Django management command to check webhook configuration and invoice status.

Usage:
    python manage.py check_webhooks
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from billing.models import ConnectedAccountInvoice


class Command(BaseCommand):
    help = 'Check webhook configuration and connected account invoice status'

    def handle(self, *args, **options):
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("WEBHOOK CONFIGURATION CHECK"))
        self.stdout.write("="*70 + "\n")
        
        # Check webhook secret
        webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', None)
        connect_webhook_secret = getattr(settings, 'STRIPE_CONNECT_WEBHOOK_SECRET', None)
        
        if webhook_secret:
            self.stdout.write(self.style.SUCCESS("✅ STRIPE_WEBHOOK_SECRET is configured"))
            self.stdout.write(f"   Value starts with: {webhook_secret[:10]}...")
        else:
            self.stdout.write(self.style.WARNING("⚠️  STRIPE_WEBHOOK_SECRET is NOT configured"))
        
        if connect_webhook_secret:
            self.stdout.write(self.style.SUCCESS("✅ STRIPE_CONNECT_WEBHOOK_SECRET is configured"))
            self.stdout.write(f"   Value starts with: {connect_webhook_secret[:10]}...")
        else:
            self.stdout.write(self.style.WARNING("⚠️  STRIPE_CONNECT_WEBHOOK_SECRET is NOT configured"))
        
        if not webhook_secret and not connect_webhook_secret:
            self.stdout.write(self.style.ERROR("\n❌ No webhook secrets configured!"))
            self.stdout.write("   Add at least one to your .env file:")
            self.stdout.write("   STRIPE_WEBHOOK_SECRET=whsec_your_secret")
            self.stdout.write("   STRIPE_CONNECT_WEBHOOK_SECRET=whsec_your_secret")
        
        # Check Stripe API key
        stripe_key = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if stripe_key:
            self.stdout.write(self.style.SUCCESS("✅ STRIPE_SECRET_KEY is configured"))
        else:
            self.stdout.write(self.style.ERROR("❌ STRIPE_SECRET_KEY is NOT configured"))
        
        self.stdout.write("\n" + "-"*70)
        self.stdout.write("WEBHOOK ENDPOINT:")
        self.stdout.write("  URL: /stripe/webhook/")
        self.stdout.write("  Handler: billing.views.stripe_webhook")
        self.stdout.write("  CSRF: Exempt (required for webhooks)")
        self.stdout.write("-"*70 + "\n")
        
        # Check invoices
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("CONNECTED ACCOUNT INVOICE STATUS"))
        self.stdout.write("="*70 + "\n")
        
        invoices = ConnectedAccountInvoice.objects.all().order_by('-created_at')
        
        if not invoices.exists():
            self.stdout.write(self.style.WARNING("⚠️  No invoices found in database"))
            self.stdout.write("\nCreate an invoice first by:")
            self.stdout.write("1. Going to /accounts/invoices/")
            self.stdout.write("2. Filling out the form and sending an invoice")
            return
        
        self.stdout.write(f"Found {invoices.count()} invoice(s):\n")
        
        for inv in invoices:
            self.stdout.write(f"Invoice ID: {inv.stripe_invoice_id or '(not created in Stripe yet)'}")
            self.stdout.write(f"  Status: {inv.status}")
            self.stdout.write(f"  Amount: {inv.amount} {inv.currency}")
            self.stdout.write(f"  Email: {inv.customer_email}")
            self.stdout.write(f"  Connected Account: {inv.connected_account}")
            
            if inv.status == 'paid':
                self.stdout.write(self.style.SUCCESS(f"  ✅ Paid at: {inv.paid_at}"))
                self.stdout.write(self.style.SUCCESS("  → Webhook is working! Invoice marked as paid."))
            elif inv.status == 'pending':
                self.stdout.write(self.style.WARNING("  ⏳ Status: Pending payment"))
                self.stdout.write(f"  → Payment link: {inv.hosted_invoice_url}")
                self.stdout.write("  → Pay this invoice to test webhook")
            elif inv.status == 'payment_failed':
                self.stdout.write(self.style.ERROR("  ❌ Payment failed"))
                self.stdout.write("  → Webhook received payment failure event")
            else:
                self.stdout.write(f"  ℹ️  Status: {inv.status}")
            
            self.stdout.write(f"  Created: {inv.created_at}")
            self.stdout.write("")
        
        # Summary
        paid_count = invoices.filter(status='paid').count()
        pending_count = invoices.filter(status='pending').count()
        failed_count = invoices.filter(status='payment_failed').count()
        
        self.stdout.write("\n" + "-"*70)
        self.stdout.write("SUMMARY:")
        self.stdout.write(f"  Paid: {paid_count}")
        self.stdout.write(f"  Pending: {pending_count}")
        self.stdout.write(f"  Failed: {failed_count}")
        self.stdout.write("-"*70 + "\n")
        
        if paid_count > 0:
            self.stdout.write(self.style.SUCCESS("✅ SUCCESS! You have paid invoices, which means webhooks are working!"))
        elif pending_count > 0:
            self.stdout.write(self.style.WARNING("⏳ You have pending invoices. Pay one to test webhook functionality."))
            self.stdout.write("\nTo test:")
            self.stdout.write("1. Copy the payment link from above")
            self.stdout.write("2. Open it in a browser")
            self.stdout.write("3. Use test card: 4242 4242 4242 4242")
            self.stdout.write("4. Complete the payment")
            self.stdout.write("5. Run this command again to see if status changed to 'paid'")
