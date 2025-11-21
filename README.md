# StripeTest

A Django application for managing user subscriptions and payments using Stripe.

## Features

- User registration and authentication
- Subscription plans (monthly/yearly)
- Stripe integration for payment processing
- Dashboard for managing subscriptions
- Payment history tracking

## Tech Stack

- Django 5.2.7
- Stripe
- PostgreSQL
- Docker

## Installation

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd stripetest
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables (Stripe API keys, database settings, etc.)

4. Run migrations:
   ```bash
   python manage.py migrate
   ```

5. Run the server:
   ```bash
   python manage.py runserver
   ```

Or use Docker:
```bash
docker-compose up --build
```

## Usage

- Access the home page at http://localhost:8000
- Register an account at /accounts/register/
- Log in at /accounts/login/
- Subscribe to a plan from the dashboard
- View and manage subscriptions at /subscriptions/

## Contributing

Contributions are welcome. Please open an issue or submit a pull request.

## License

MIT License
