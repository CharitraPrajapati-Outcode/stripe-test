from django.urls import path
from django.contrib.auth import views as auth_views
from .views import (
    RegisterView,
    DashboardView,
    SubscribeView,
    CreateSubscriptionView,
    CancelSubscriptionView,
    UserSubscriptionListView,
    RefreshSubscriptionsAPIView,
    logout_view,
    subscriptions_event_stream,
    connect_start,
    connect_return,
    connect_refresh,
    connect_info,
    connect_remove,
    invoices_view,
)
from .forms import CustomAuthenticationForm

app_name = 'accounts'

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html', authentication_form=CustomAuthenticationForm), name='login'),
    # Allow GET/POST logout via our custom view to avoid HTTP 405 on GET
    path('logout/', logout_view, name='logout'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    path('subscribe/<str:price_id>/', SubscribeView.as_view(), name='subscribe'),
    path('create-subscription/', CreateSubscriptionView.as_view(), name='create_subscription'),
    path('subscription/<str:sub_id>/cancel/', CancelSubscriptionView.as_view(), name='cancel_subscription'),
    path('api/refresh-subscriptions/', RefreshSubscriptionsAPIView.as_view(), name='api_refresh_subscriptions'),
    path('subscriptions/', UserSubscriptionListView.as_view(), name='subscriptions'),
    path('subscriptions/stream/', subscriptions_event_stream, name='subscriptions_stream'),
    path('connect/start/', connect_start, name='connect_start'),
    path('connect/return/', connect_return, name='connect_return'),
    path('connect/refresh/', connect_refresh, name='connect_refresh'),
    path('connect/info/', connect_info, name='connect_info'),
    path('connect/remove/', connect_remove, name='connect_remove'),
    path('invoices/', invoices_view, name='invoices'),
]
