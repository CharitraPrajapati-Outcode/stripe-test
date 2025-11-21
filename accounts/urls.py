from django.urls import path
from django.contrib.auth import views as auth_views
from .views import RegisterView, DashboardView, SubscribeView, CreateSubscriptionView, CancelSubscriptionView, UserSubscriptionListView
from .forms import CustomAuthenticationForm

app_name = 'accounts'

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html', authentication_form=CustomAuthenticationForm), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('subscribe/<str:price_id>/', SubscribeView.as_view(), name='subscribe'),
    path('create-subscription/', CreateSubscriptionView.as_view(), name='create_subscription'),
    path('subscription/<str:sub_id>/cancel/', CancelSubscriptionView.as_view(), name='cancel_subscription'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('subscriptions/', UserSubscriptionListView.as_view(), name='subscriptions'),
]
