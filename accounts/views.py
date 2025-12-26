from django.views.generic import CreateView, TemplateView, ListView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from decimal import Decimal
import json
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from .forms import CustomUserCreationForm
from django.views import View
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from django.http import JsonResponse
from django.http import HttpResponseNotAllowed
from django.contrib.auth import logout
from django.contrib.auth.models import Group
from django.http import StreamingHttpResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt
import stripe
from billing.models import ConnectedAccountInvoice
from django.utils import timezone as dj_timezone
from django.core.mail import send_mail


def plan_name_to_role(plan_name: str) -> str:
    """Map plan name to one of the role groups (lower-case)."""
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

from billing.models import SubscriptionPlan, UserSubscription, SubscriptionPayment
from billing.stripe_utils import StripeManager
from django.utils import timezone
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger


class RegisterView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('accounts:login')


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stripe_mgr = StripeManager()

        # Fetch available subscription plans from database
        plans = []
        try:
            for sp in SubscriptionPlan.objects.filter(active=True):
                plans.append({
                    'id': sp.stripe_price_id,
                    'product_name': sp.name,
                    'amount': f"{sp.price:.2f}",
                    'currency': 'USD',
                    'interval': sp.interval,
                })
        except Exception as e:
            context['stripe_error'] = str(e)

        # Fetch account information
        account_name = stripe_mgr.get_account_info()
        context['plans'] = plans
        context['stripe_account_name'] = account_name

        # If user has connected account id, fetch its status to determine if onboarding
        # needs to be resumed (e.g. bank details or verification incomplete).
        try:
            connected_acct_id = getattr(self.request.user, 'stripe_connected_account_id', None)
            context['connected_acct_id'] = connected_acct_id
            context['connected_needs_onboarding'] = False
            context['connected_account'] = None
            if connected_acct_id and getattr(settings, 'STRIPE_SECRET_KEY', None):
                try:
                    acct = stripe_mgr.retrieve_account(connected_acct_id)
                    # normalize dict/object
                    acct_dict = acct if isinstance(acct, dict) else acct.to_dict() if hasattr(acct, 'to_dict') else None
                    context['connected_account'] = acct_dict
                    # Decide whether onboarding needs to continue.
                    # If charges_enabled or payouts_enabled are False, or requirements have currently_due items, resume onboarding.
                    charges_enabled = acct_dict.get('charges_enabled') if acct_dict else False
                    payouts_enabled = acct_dict.get('payouts_enabled') if acct_dict else False
                    requirements = acct_dict.get('requirements', {}) if acct_dict else {}
                    currently_due = requirements.get('currently_due') if isinstance(requirements, dict) else None
                    needs = False
                    if not charges_enabled or not payouts_enabled:
                        needs = True
                    if currently_due and len(currently_due) > 0:
                        needs = True
                    context['connected_needs_onboarding'] = needs
                except Exception:
                    # If Stripe call fails, leave flags as defaults (don't break the dashboard)
                    context['connected_needs_onboarding'] = False
                    context['connected_account'] = None
        except Exception:
            context['connected_acct_id'] = None
            context['connected_needs_onboarding'] = False
            context['connected_account'] = None

        # Subscriptions are now kept in sync via Stripe webhooks.
        # Avoid making per-request API calls to Stripe from the dashboard
        # to reduce latency and unnecessary external requests. Use the
        # manual refresh button (or webhooks) to update subscription state.

        # Split subscriptions into active list for dashboard UI
        try:
            all_subs = self.request.user.subscriptions.all()
            active_subs = all_subs.filter(status__in=['active', 'trialing'])
            context['active_subscriptions'] = active_subs
        except Exception:
            context['active_subscriptions'] = []

        # Determine user's roles (all matching role groups). Default to ['free'] if none.
        try:
            role_candidates = ['free', 'athlete', 'host', 'guest']
            user_roles = [r for r in role_candidates if self.request.user.groups.filter(name=r).exists()]
            if not user_roles:
                user_roles = ['free']
            context['user_roles'] = user_roles
        except Exception:
            context['user_roles'] = ['free']

        # Which price ids the user is already subscribed to (active/non-cancelled)
        try:
            subscribed_price_ids = list(self.request.user.subscriptions.filter(status__in=['active','trialing','past_due','incomplete']).values_list('plan__stripe_price_id', flat=True))
            # filter out None
            subscribed_price_ids = [x for x in subscribed_price_ids if x]
            context['subscribed_price_ids'] = subscribed_price_ids
        except Exception:
            context['subscribed_price_ids'] = []

        return context



class UserSubscriptionListView(LoginRequiredMixin, ListView):
    """ListView to show the current user's subscriptions split into active and past/cancelled."""
    model = UserSubscription
    template_name = 'subscriptions_list.html'
    context_object_name = 'subscriptions'

    def get_queryset(self):
        # return all subscriptions for the current user (ordered newest first)
        return UserSubscription.objects.filter(user=self.request.user).order_by('-created_at')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        ctx['active_subscriptions'] = qs.filter(status__in=['active', 'trialing'])
        # Paginate cancelled/past subscriptions (10 per page)
        cancelled_qs = qs.filter(status='canceled').order_by('-created_at')
        page_num = self.request.GET.get('page', 1)
        paginator = Paginator(cancelled_qs, 10)
        try:
            cancelled_page = paginator.page(page_num)
        except PageNotAnInteger:
            cancelled_page = paginator.page(1)
        except EmptyPage:
            cancelled_page = paginator.page(paginator.num_pages)

        ctx['cancelled_subscriptions_page'] = cancelled_page
        # Backwards compatibility: keep `cancelled_subscriptions` as the current page's object list
        ctx['cancelled_subscriptions'] = cancelled_page.object_list
        # Determine user's roles (all matching role groups). Default to ['free'] if none.
        try:
            role_candidates = ['free', 'athlete', 'host', 'guest']
            user_roles = [r for r in role_candidates if self.request.user.groups.filter(name=r).exists()]
            if not user_roles:
                user_roles = ['free']
            ctx['user_roles'] = user_roles
        except Exception:
            ctx['user_roles'] = ['free']
        return ctx


def subscriptions_event_stream(request):
    """Server-Sent Events stream that notifies the logged-in user when their subscriptions change.

    This simple implementation polls the user's subscriptions `updated_at` timestamp
    and emits an event when it changes. It avoids adding external dependencies and
    works with existing webhook handlers which update `UserSubscription.updated_at`.
    """
    def event_generator(user):
        import time, json
        last_sent = None
        try:
            latest = user.subscriptions.order_by('-updated_at').values_list('updated_at', flat=True).first()
            last_sent = latest.isoformat() if latest else None
        except Exception:
            last_sent = None

        while True:
            try:
                latest = user.subscriptions.order_by('-updated_at').values_list('updated_at', flat=True).first()
                latest_iso = latest.isoformat() if latest else None
                if latest_iso != last_sent:
                    payload = {'event': 'subscriptions_updated', 'latest': latest_iso}
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_sent = latest_iso
                time.sleep(2)
            except GeneratorExit:
                break
            except Exception:
                # On transient error, wait and continue polling
                time.sleep(2)
                continue

    # Ensure this is a GET request only
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    # If the user is not authenticated, return 403 rather than a redirect
    # (EventSource clients do not handle redirects well). This results in a
    # clear failure on the client and avoids HTML login pages being streamed.
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return HttpResponseForbidden('Authentication required')

    response = StreamingHttpResponse(event_generator(request.user), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    return response


class SubscribeView(LoginRequiredMixin, View):
    def get(self, request, price_id):
        stripe_mgr = StripeManager()
        publishable = getattr(settings, 'STRIPE_PUBLISHABLE_KEY', '')

        if not getattr(settings, 'STRIPE_SECRET_KEY', None):
            messages.error(request, 'Stripe secret key not configured.')
            return redirect('accounts:dashboard')

        user = request.user
        # Ensure customer exists
        try:
            customer_id = stripe_mgr.get_or_create_customer(user)
        except Exception as e:
            messages.error(request, str(e))
            return redirect('accounts:dashboard')

        # List existing payment methods
        pms = stripe_mgr.list_payment_methods(customer_id)

        # Create a SetupIntent for new card entry
        try:
            setup_intent = stripe_mgr.create_setup_intent(customer_id)
        except Exception as e:
            messages.error(request, str(e))
            return redirect('accounts:dashboard')

        # Retrieve price info for display
        try:
            price = stripe_mgr.get_price(price_id)
        except Exception as e:
            messages.error(request, str(e))
            return redirect('accounts:dashboard')

        price_amount = stripe_mgr.get_price_amount(price)

        context = {
            'price_id': price_id,
            'price': price,
            'price_amount': f"{price_amount:.2f}",
            'price_currency': getattr(price, 'currency', '').upper(),
            'payment_methods': pms,
            'setup_client_secret': setup_intent.client_secret,
            'stripe_publishable_key': publishable,
        }
        # Indicate if the current user already has an active subscription for this price
        try:
            already = UserSubscription.objects.filter(user=request.user, plan__stripe_price_id=price_id).exclude(status='canceled').exists()
            context['already_subscribed'] = already
        except Exception:
            context['already_subscribed'] = False
        return render(request, 'subscribe.html', context)


class CreateSubscriptionView(LoginRequiredMixin, View):
    def post(self, request):
        from django.contrib import messages
        import logging
        logger = logging.getLogger(__name__)
        
        stripe_mgr = StripeManager()
        price_id = request.POST.get('price_id')
        payment_method = request.POST.get('payment_method')

        logger.debug('CreateSubscriptionView POST: price_id=%s, payment_method=%s', price_id, payment_method)
        logger.debug('POST data: %s', dict(request.POST))

        if not getattr(settings, 'STRIPE_SECRET_KEY', None):
            messages.error(request, 'Stripe secret key not configured.')
            return redirect('accounts:dashboard')

        if not price_id or not payment_method:
            logger.error('Missing price_id or payment_method: price_id=%s, payment_method=%s', price_id, payment_method)
            messages.error(request, 'Missing price ID or payment method.')
            return redirect('accounts:subscribe', price_id=price_id)

        user = request.user

        try:
            logger.debug('Creating subscription for user %s', user)
            # Get or create customer
            customer_id = stripe_mgr.get_or_create_customer(user)
            logger.debug('Customer ID: %s', customer_id)
            # Retrieve or create local SubscriptionPlan BEFORE creating a Stripe subscription
            plan_obj = None
            try:
                plan_obj = SubscriptionPlan.objects.filter(stripe_price_id=price_id).first()
            except Exception:
                plan_obj = None

            if not plan_obj:
                # fetch price from Stripe to populate local plan
                try:
                    price = stripe_mgr.get_price(price_id)
                    prod = price.product if hasattr(price, 'product') else None
                    prod_name = prod.get('name') if isinstance(prod, dict) else getattr(prod, 'name', None) if prod else getattr(price, 'id', price_id)
                    unit_amount = getattr(price, 'unit_amount', 0) or 0
                    amount_decimal = (int(unit_amount) / 100.0) if unit_amount else 0
                    plan_obj = SubscriptionPlan.objects.create(
                        name=prod_name or price_id,
                        stripe_price_id=price_id,
                        price=amount_decimal,
                        interval=(price.recurring.get('interval') if getattr(price, 'recurring', None) else 'month')
                    )
                except Exception as e:
                    logger.exception('Error fetching price to create local plan: %s', str(e))
                    messages.error(request, f'Unable to retrieve price information: {str(e)}')
                    return redirect('accounts:dashboard')

            # Prevent duplicate subscriptions to the same plan if user already has a non-cancelled subscription
            try:
                # Match by stripe price id to be robust even if local plan object lookup differed
                duplicate_qs = UserSubscription.objects.filter(user=user, plan__stripe_price_id=price_id).exclude(status='canceled')
                if duplicate_qs.exists():
                    logger.info('Duplicate subscription prevented for user %s and price %s', user, price_id)
                    messages.error(request, 'You already have an active subscription for this plan.')
                    return redirect('accounts:dashboard')
            except Exception:
                # If anything goes wrong with the duplicate check, log and continue conservatively
                logger.exception('Error checking for duplicate subscriptions for user %s and price_id %s', user, price_id)

            # Create subscription on Stripe
            sub = stripe_mgr.create_subscription(customer_id, price_id, payment_method)
            logger.debug('Subscription created: %s', sub.get('id') if isinstance(sub, dict) else getattr(sub, 'id', None))

            # Create or update a minimal local subscription record. Detailed
            # status, payments and period dates will be populated by webhooks.
            stripe_sub_id = (sub.get('id') if isinstance(sub, dict) else getattr(sub, 'id', None))
            stripe_status = (sub.get('status') if isinstance(sub, dict) else getattr(sub, 'status', None))
            usub, _ = UserSubscription.objects.update_or_create(
                stripe_subscription_id=stripe_sub_id,
                defaults={
                    'user': user,
                    'plan': plan_obj,
                    'status': stripe_status or '',
                }
            )
            logger.info('Subscription created successfully for user %s', user)

            # We intentionally do not record invoice/payment details here. The
            # webhook handler listens for invoice/payment events and will
            # create SubscriptionPayment rows when appropriate.

            messages.success(request, 'Subscription created successfully.')
            return redirect('accounts:dashboard')

        except Exception as e:
            logger.exception('Error creating subscription: %s', str(e))
            messages.error(request, f'Error creating subscription: {str(e)}')
            return redirect('accounts:subscribe', price_id=price_id)


class CancelSubscriptionView(LoginRequiredMixin, View):
    """Cancel a Stripe subscription either immediately or at period end."""

    def post(self, request, sub_id):
        stripe_mgr = StripeManager()

        if not getattr(settings, 'STRIPE_SECRET_KEY', None):
            messages.error(request, 'Stripe secret key not configured.')
            return redirect('accounts:dashboard')

        user = request.user

        # Ensure this subscription belongs to the user locally (best-effort)
        usub = UserSubscription.objects.filter(stripe_subscription_id=sub_id, user=user).first()
        if not usub:
            msg = 'Subscription not found for current user.'
            messages.error(request, msg)
            # Return JSON for AJAX, otherwise redirect back to referer or dashboard
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': msg}, status=404)
            next_url = request.POST.get('next') or request.GET.get('next') or request.META.get('HTTP_REFERER') or reverse('accounts:dashboard')
            return redirect(next_url)

        when = request.POST.get('when', 'period_end')

        try:
            if when == 'now':
                # Cancel immediately (if not already cancelled)
                if usub.status == 'canceled':
                    messages.info(request, 'Subscription is already cancelled.')
                else:
                    # Request immediate cancellation at Stripe and let webhook update local state
                    try:
                        stripe_mgr.cancel_subscription(sub_id, at_period_end=False)
                        success_msg = 'Requested immediate cancellation — will be reflected after webhook processing.'
                        messages.success(request, success_msg)
                        # Immediately remove the role corresponding to this subscription's plan
                        try:
                            # Determine role from local subscription plan
                            role = plan_name_to_role(usub.plan.name if usub.plan else None)
                            g = Group.objects.filter(name=role).first()
                            if g and request.user.groups.filter(pk=g.pk).exists():
                                request.user.groups.remove(g)
                                # If user now has no role groups among candidates, add 'free'
                                role_candidates = ['free', 'athlete', 'host', 'guest']
                                has_role = False
                                for r in role_candidates:
                                    gg = Group.objects.filter(name=r).first()
                                    if gg and request.user.groups.filter(pk=gg.pk).exists():
                                        has_role = True
                                        break
                                if not has_role:
                                    free_group, _ = Group.objects.get_or_create(name='free')
                                    request.user.groups.add(free_group)
                        except Exception:
                            pass
                        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                            return JsonResponse({'success': True, 'message': success_msg})
                    except Exception as e:
                        # Keep original Stripe message for logs but present a user-friendly message
                        err_msg = str(e)
                        messages.error(request, err_msg)
                        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': err_msg}, status=400)
            else:
                # Cancel at period end
                try:
                    stripe_mgr.cancel_subscription(sub_id, at_period_end=True)
                    success_msg = 'Requested cancellation at period end — will be reflected after webhook processing.'
                    messages.success(request, success_msg)
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                        return JsonResponse({'success': True, 'message': success_msg})
                except Exception as e:
                    err_msg = str(e)
                    messages.error(request, err_msg)
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                        return JsonResponse({'success': False, 'message': err_msg}, status=400)
        except Exception as e:
            messages.error(request, f'Error cancelling subscription: {str(e)}')
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': str(e)}, status=500)

        # Non-AJAX: redirect back to `next` param if present, then referer, else dashboard
        next_url = request.POST.get('next') or request.GET.get('next') or request.META.get('HTTP_REFERER') or reverse('accounts:dashboard')
        return redirect(next_url)


class RefreshSubscriptionsAPIView(LoginRequiredMixin, View):
    """API endpoint to refresh user's subscriptions from Stripe."""

    def post(self, request):
        """Sync subscription data from Stripe to local DB and return updated count."""
        stripe_mgr = StripeManager()
        user = request.user
        updated_count = 0
        errors = []

        try:
            # Iterate through user's subscriptions and refresh from Stripe
            for usub in user.subscriptions.all():
                if not usub.stripe_subscription_id:
                    continue

                try:
                    # Fetch latest subscription data from Stripe
                    remote = stripe_mgr.retrieve_subscription(usub.stripe_subscription_id)
                    data = stripe_mgr.extract_subscription_data(remote)

                    # Check for updates
                    updated = False
                    if data['status'] and data['status'] != usub.status:
                        usub.status = data['status']
                        updated = True
                    if data['current_period_start'] and data['current_period_start'] != usub.current_period_start:
                        usub.current_period_start = data['current_period_start']
                        updated = True
                    if data['current_period_end'] and data['current_period_end'] != usub.current_period_end:
                        usub.current_period_end = data['current_period_end']
                        updated = True
                    if data['cancel_at_period_end'] is not None and data['cancel_at_period_end'] != usub.cancel_at_period_end:
                        usub.cancel_at_period_end = data['cancel_at_period_end']
                        updated = True
                    if data['canceled_at'] and data['canceled_at'] != usub.cancelled_at:
                        usub.cancelled_at = data['canceled_at']
                        updated = True

                    if updated:
                        usub.save()
                        updated_count += 1

                except Exception as e:
                    errors.append(f'Error syncing subscription {usub.stripe_subscription_id}: {str(e)}')

        except Exception as e:
            errors.append(f'Error refreshing subscriptions: {str(e)}')

        # Return JSON response
        return JsonResponse({
            'success': len(errors) == 0,
            'updated_count': updated_count,
            'errors': errors,
            'message': f'Refreshed {updated_count} subscription(s)' if len(errors) == 0 else 'Refresh completed with errors'
        })


def logout_view(request):
    """Log out the user. Accept both GET and POST to support browser logout via link.

    Using GET for logout is acceptable for many apps; if you prefer POST-only,
    replace this with Django's `LogoutView` and ensure your logout form uses POST
    with a valid CSRF token.
    """
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])

    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('accounts:login')


@login_required
def connect_start(request):
    """Begin Stripe Connect onboarding for the logged-in user.

    - Creates a Stripe connected account (Express) if the user doesn't have one.
    - Generates an account link and redirects the user to Stripe's onboarding flow.
    """
    stripe_mgr = StripeManager()

    user = request.user
    try:
        acct_id = getattr(user, 'stripe_connected_account_id', None)
        if not acct_id:
            acct = stripe_mgr.create_connected_account()
            acct_id = acct.get('id') if isinstance(acct, dict) else getattr(acct, 'id', None)
            if acct_id:
                user.stripe_connected_account_id = acct_id
                user.save(update_fields=['stripe_connected_account_id'])

        # Build absolute URLs for refresh and return
        refresh_url = request.build_absolute_uri(reverse('accounts:connect_refresh'))
        return_url = request.build_absolute_uri(reverse('accounts:connect_return'))

        link = stripe_mgr.create_account_link(acct_id, refresh_url=refresh_url, return_url=return_url)
        link_url = link.get('url') if isinstance(link, dict) else getattr(link, 'url', None)
        if link_url:
            return redirect(link_url)
        else:
            messages.error(request, 'Could not create Stripe onboarding link.')
            return redirect('accounts:dashboard')
    except Exception as e:
        # Provide a clearer message when the platform account is not enabled for Connect
        msg = str(e)
        if 'You can only create new accounts if you\'ve signed up for Connect' in msg or 'signed up for Connect' in msg:
            messages.error(request, 'Your Stripe platform account is not enabled for Connect.\n\nVisit https://dashboard.stripe.com/settings/connect to enable Connect for your account, or see https://stripe.com/docs/connect for details.')
        else:
            messages.error(request, f'Error starting Stripe onboarding: {msg}')
        # Log full exception to server logs for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.exception('Error during connect_start for user %s: %s', getattr(request.user, 'pk', 'unknown'), msg)
        return redirect('accounts:dashboard')


@require_GET
def connect_return(request):
    """Return URL after Stripe onboarding completes. Redirect to dashboard with a message."""
    messages.success(request, 'Welcome back — your Stripe onboarding may be in progress. It can take a few minutes to reflect in your account.')
    return redirect('accounts:dashboard')


@login_required
def connect_info(request):
    """Display Stripe connected account details on a separate page."""
    stripe_mgr = StripeManager()
    context = {}
    try:
        connected_acct_id = getattr(request.user, 'stripe_connected_account_id', None)
        context['connected_acct_id'] = connected_acct_id
        context['connected_needs_onboarding'] = False
        context['connected_account'] = None
        if connected_acct_id and getattr(settings, 'STRIPE_SECRET_KEY', None):
            try:
                acct = stripe_mgr.retrieve_account(connected_acct_id)
                acct_dict = acct if isinstance(acct, dict) else acct.to_dict() if hasattr(acct, 'to_dict') else None
                context['connected_account'] = acct_dict
                # Extract dashboard timezone if present. Check common locations:
                # 1) acct.dashboard.timezone
                # 2) acct.settings.dashboard.timezone
                # 3) acct.settings.time_zone (legacy)
                try:
                    tz = None
                    if isinstance(acct_dict, dict):
                        top_dash = acct_dict.get('dashboard')
                        if isinstance(top_dash, dict):
                            tz = top_dash.get('timezone') or top_dash.get('display_name')

                        settings_obj = acct_dict.get('settings')
                        if not tz and isinstance(settings_obj, dict):
                            settings_dash = settings_obj.get('dashboard')
                            if isinstance(settings_dash, dict):
                                tz = settings_dash.get('timezone') or settings_dash.get('display_name')

                        if not tz and isinstance(settings_obj, dict):
                            tz = settings_obj.get('time_zone') or settings_obj.get('timezone')

                    context['dashboard_timezone'] = tz
                except Exception:
                    context['dashboard_timezone'] = None

                # Normalize individual person info (format created timestamp if available)
                try:
                    ind = acct_dict.get('individual') if isinstance(acct_dict, dict) else None
                    if isinstance(ind, dict):
                        ind_copy = ind.copy()
                        created = ind_copy.get('created')
                        if isinstance(created, (int, float)):
                            try:
                                from datetime import datetime
                                from datetime import timezone as _dt_tz
                                dt = datetime.fromtimestamp(int(created), tz=_dt_tz.utc)
                                ind_copy['created_human'] = dt.isoformat()
                            except Exception:
                                ind_copy['created_human'] = str(created)
                        context['individual_info'] = ind_copy
                    else:
                        context['individual_info'] = None
                except Exception:
                    context['individual_info'] = None
                charges_enabled = acct_dict.get('charges_enabled') if acct_dict else False
                payouts_enabled = acct_dict.get('payouts_enabled') if acct_dict else False
                requirements = acct_dict.get('requirements', {}) if acct_dict else {}
                currently_due = requirements.get('currently_due') if isinstance(requirements, dict) else None
                needs = False
                if not charges_enabled or not payouts_enabled:
                    needs = True
                if currently_due and len(currently_due) > 0:
                    needs = True
                context['connected_needs_onboarding'] = needs
            except Exception:
                context['connected_needs_onboarding'] = False
                context['connected_account'] = None
    except Exception:
        context['connected_acct_id'] = None
        context['connected_needs_onboarding'] = False
        context['connected_account'] = None

    return render(request, 'connected_account.html', context)


@login_required
def connect_remove(request):
    """Remove the connected Stripe account: delete on Stripe and clear user's field."""
    from django.http import HttpResponseNotAllowed
    stripe_mgr = StripeManager()

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    acct_id = getattr(request.user, 'stripe_connected_account_id', None)
    if not acct_id:
        messages.error(request, 'No connected Stripe account to remove.')
        return redirect('accounts:connect_info')

    try:
        # Attempt to delete on Stripe
        try:
            # Use the StripeManager helper which sets API key
            stripe_mgr.delete_connected_account(acct_id)
        except Exception as e:
            # Report Stripe error and do not clear local reference
            messages.error(request, f'Error deleting connected account on Stripe: {str(e)}')
            return redirect('accounts:connect_info')

        # Clear local field
        request.user.stripe_connected_account_id = None
        request.user.save(update_fields=['stripe_connected_account_id'])
        messages.success(request, 'Connected Stripe account removed.')
        return redirect('accounts:dashboard')
    except Exception as e:
        messages.error(request, f'Error removing connected account: {str(e)}')
        return redirect('accounts:connect_info')


@require_GET
def connect_refresh(request):
    """Refresh URL when Stripe onboarding is cancelled/closed; redirect back to dashboard."""
    messages.info(request, 'Stripe onboarding was not completed. You can try again.')
    return redirect('accounts:dashboard')


@login_required
def invoices_view(request):
    """Invoices page: allow sending an invoice (create on connected account) to an email.

    This view will create a Customer on the connected account, create an InvoiceItem
    and an Invoice, finalize it and record the resulting invoice details in the
    `ConnectedAccountInvoice` model. It requires that the current user has
    `stripe_connected_account_id` set and the platform `STRIPE_SECRET_KEY`.
    """
    stripe_mgr = StripeManager()
    connected_acct_id = getattr(request.user, 'stripe_connected_account_id', None)
    invoices = []
    page_obj = None
    
    if connected_acct_id:
        invoices_list = ConnectedAccountInvoice.objects.filter(connected_account=connected_acct_id).order_by('-created_at')
        
        # Pagination: 10 invoices per page
        paginator = Paginator(invoices_list, 5)
        page_number = request.GET.get('page', 1)
        
        try:
            page_obj = paginator.get_page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.get_page(1)
        except EmptyPage:
            page_obj = paginator.get_page(paginator.num_pages)
        
        invoices = page_obj

    if request.method == 'POST':
        # Get form fields
        email = request.POST.get('email')
        amount = request.POST.get('amount')
        currency = request.POST.get('currency') or 'usd'
        description = request.POST.get('description') or 'Invoice from platform'

        if not connected_acct_id:
            messages.error(request, 'No connected Stripe account found for your user.')
            return redirect('accounts:invoices')

        # Validate and parse amount
        if not amount:
            messages.error(request, 'Amount is required.')
            return redirect('accounts:invoices')

        try:
            from decimal import Decimal
            amt_decimal = Decimal(str(amount).strip())
            amt_cents = int(amt_decimal * 100)
        except Exception as e:
            messages.error(request, f'Invalid amount format: {amount}. Please enter a valid number.')
            return redirect('accounts:invoices')

        if amt_cents <= 0:
            messages.error(request, 'Amount must be greater than zero.')
            return redirect('accounts:invoices')

        # Create local ConnectedAccountInvoice record with pending status
        record = ConnectedAccountInvoice.objects.create(
            connected_account=connected_acct_id,
            customer_email=email,
            amount=amt_decimal,
            currency=currency.upper(),
            status='pending'
        )

        # Create Stripe invoice on connected account
        try:
            if not getattr(settings, 'STRIPE_SECRET_KEY', None):
                raise Exception('Stripe secret key not configured.')

            stripe.api_key = settings.STRIPE_SECRET_KEY

            # Create customer on connected account
            cust = stripe.Customer.create(
                email=email,
                name=email,
                stripe_account=connected_acct_id
            )

            # Create invoice first (draft state)
            invoice = stripe.Invoice.create(
                customer=cust.id,
                collection_method='send_invoice',
                days_until_due=7,
                auto_advance=False,  # Prevent auto-finalization
                stripe_account=connected_acct_id
            )

            # Add invoice item to the draft invoice
            stripe.InvoiceItem.create(
                customer=cust.id,
                invoice=invoice.id,  # Attach to specific invoice
                amount=amt_cents,
                currency=currency.lower(),
                description=description,
                stripe_account=connected_acct_id
            )

            # Now finalize the invoice to generate hosted payment URL
            finalized = stripe.Invoice.finalize_invoice(invoice.id, stripe_account=connected_acct_id)

            # Get hosted invoice URL
            hosted_url = finalized.get('hosted_invoice_url') if isinstance(finalized, dict) else getattr(finalized, 'hosted_invoice_url', None)

            # Update local record
            record.stripe_invoice_id = finalized.get('id') if isinstance(finalized, dict) else getattr(finalized, 'id', None)
            record.hosted_invoice_url = hosted_url
            record.invoice_pdf_url = finalized.get('invoice_pdf') if isinstance(finalized, dict) else getattr(finalized, 'invoice_pdf', None)
            record.save()

            # Send email with payment link
            if hosted_url:
                subject = f'Payment Request from {request.user.get_full_name() or request.user.username}'
                body = f'''Hello,

                You have received a payment request for {amt_decimal} {currency.upper()}.

                Click the button below to view and pay the invoice:

                {hosted_url}

                Thank you!
                '''
                from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or 'no-reply@example.com'
                send_mail(subject, body, from_email, [email], fail_silently=False)
                
                messages.success(request, f'Invoice created and payment link sent to {email}.')
            else:
                messages.warning(request, 'Invoice created but no payment link was generated.')
            
            return redirect('accounts:invoices')

        except Exception as e:
            # Update record status to error
            record.status = 'error'
            record.metadata = {'error': str(e)}
            record.save()
            messages.error(request, f'Error creating invoice: {str(e)}')
            return redirect('accounts:invoices')

    return render(request, 'invoices.html', {
        'connected_acct_id': connected_acct_id, 
        'invoices': invoices,
        'page_obj': page_obj
    })

