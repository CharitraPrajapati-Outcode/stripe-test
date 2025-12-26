from django.core.management.base import BaseCommand
from django.conf import settings
import stripe
from decimal import Decimal

from billing.models import SubscriptionPlan


class Command(BaseCommand):
    help = 'Sync active Stripe products and prices with SubscriptionPlan in the database'

    def handle(self, *args, **options):
        secret = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if not secret:
            self.stderr.write('STRIPE_SECRET_KEY not configured in settings')
            return

        stripe.api_key = secret

        self.stdout.write('Fetching active prices from Stripe...')
        try:
            prices = stripe.Price.list(active=True, expand=['data.product']).auto_paging_iter()
        except Exception as e:
            self.stderr.write(f'Failed to list prices from Stripe: {e}')
            return

        stripe_price_ids = set()
        count_new = 0
        count_updated = 0

        for price in prices:
            price_id = price.get('id')
            stripe_price_ids.add(price_id)

            product = price.get('product')
            product_name = None
            if isinstance(product, dict):
                product_name = product.get('name')
            else:
                # if product is just id string, attempt to retrieve
                try:
                    prod_obj = stripe.Product.retrieve(product)
                    product_name = prod_obj.get('name')
                except Exception:
                    product_name = None

            # Price amount and currency
            unit_amount = price.get('unit_amount') or price.get('unit_amount_decimal')
            if unit_amount is None:
                price_decimal = Decimal('0.00')
            else:
                try:
                    price_decimal = Decimal(int(unit_amount)) / Decimal(100)
                except Exception:
                    price_decimal = Decimal(str(unit_amount))

            # Interval (e.g. month, year)
            recurring = price.get('recurring')
            interval = recurring.get('interval') if recurring else 'month'

            # Check if SubscriptionPlan exists for this price id
            try:
                plan, created = SubscriptionPlan.objects.update_or_create(
                    stripe_price_id=price_id,
                    defaults={
                        'name': product_name or price_id,
                        'price': price_decimal,
                        'interval': interval,
                        'active': True,
                    }
                )
                if created:
                    count_new += 1
                else:
                    count_updated += 1
            except Exception as e:
                self.stderr.write(f'Failed to update or create plan for price {price_id}: {e}')

        # Optionally deactivate SubscriptionPlan records not found in Stripe prices
        try:
            to_deactivate = SubscriptionPlan.objects.filter(active=True).exclude(stripe_price_id__in=stripe_price_ids)
            deactivated_count = to_deactivate.update(active=False)
            self.stdout.write(f'Deactivated {deactivated_count} plans not found in Stripe.')
        except Exception as e:
            self.stderr.write(f'Failed to deactivate missing plans: {e}')

        self.stdout.write(f'Synced Stripe prices: {count_new} new plans created, {count_updated} plans updated.')
