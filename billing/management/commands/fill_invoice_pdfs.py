import logging
from django.core.management.base import BaseCommand
from billing.models import SubscriptionPayment
from billing.stripe_utils import StripeManager


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Populate invoice_pdf_url for SubscriptionPayment records by fetching invoice data from Stripe.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0, help='Limit number of records processed (0 = all)')
        parser.add_argument('--dry-run', action='store_true', help='Do not save changes; show what would be updated')
        parser.add_argument('--batch', type=int, default=50, help='Number of records to process per iteration (for logging)')

    def handle(self, *args, **options):
        limit = options.get('limit') or 0
        dry_run = options.get('dry_run')
        batch = options.get('batch') or 50

        stripe_mgr = StripeManager()

        qs = SubscriptionPayment.objects.filter(stripe_invoice_id__isnull=False, invoice_pdf_url__isnull=True).order_by('id')
        total_to_process = qs.count()
        if limit and limit > 0:
            qs = qs[:limit]
            total_to_process = min(total_to_process, limit)

        self.stdout.write(self.style.NOTICE(f'Found {total_to_process} payments missing invoice_pdf_url'))

        processed = 0
        updated = 0
        failed = 0

        for p in qs.iterator():
            processed += 1
            invoice_id = p.stripe_invoice_id
            if not invoice_id:
                continue

            try:
                # Try to retrieve invoice via StripeManager which expands payment_intent/charges
                full_invoice = stripe_mgr.retrieve_invoice(invoice_id)
                # full_invoice may be dict-like or object
                invoice_pdf = None
                if isinstance(full_invoice, dict):
                    invoice_pdf = full_invoice.get('invoice_pdf') or full_invoice.get('hosted_invoice_url')
                else:
                    invoice_pdf = getattr(full_invoice, 'invoice_pdf', None) or getattr(full_invoice, 'hosted_invoice_url', None)

                if invoice_pdf:
                    self.stdout.write(f'[{processed}/{total_to_process}] Invoice {invoice_id}: found PDF URL')
                    if not dry_run:
                        p.invoice_pdf_url = invoice_pdf
                        p.save(update_fields=['invoice_pdf_url'])
                        updated += 1
                else:
                    self.stdout.write(f'[{processed}/{total_to_process}] Invoice {invoice_id}: no PDF/hosted URL available')

            except Exception as e:
                failed += 1
                logger.exception('Error fetching invoice %s: %s', invoice_id, str(e))
                self.stdout.write(self.style.WARNING(f'[{processed}/{total_to_process}] Failed to fetch invoice {invoice_id}: {e}'))

            # Periodic progress
            if processed % batch == 0:
                self.stdout.write(f'Processed {processed}/{total_to_process} (updated: {updated}, failed: {failed})')

        self.stdout.write(self.style.SUCCESS(f'Done. Processed={processed}, updated={updated}, failed={failed} (dry_run={dry_run})'))
