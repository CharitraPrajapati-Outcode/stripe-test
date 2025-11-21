# TODO: Update Subscription Display for Cancel at Period End

## Tasks
- [x] Update the subscription status display in `templates/subscriptions_list.html` to show "Ending at" instead of "Next billing" when `cancel_at_period_end` is true.
- [x] Disable the "Cancel at period end" button in `templates/subscriptions_list.html` when `cancel_at_period_end` is true, similar to `templates/dashboard.html`.
