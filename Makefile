
.PHONY: build
build:
	docker compose -f docker-compose.yml build

.PHONY: run
run:
	docker compose -f docker-compose.yml up $(d)

.PHONY: stop
stop:
	docker compose -f docker-compose.yml down

.PHONY: migrate
migrate:
	docker compose exec -it web python manage.py migrate $(app_name) $(migration_name)

.PHONY: migrations
migrations:
	docker compose exec -it web python manage.py makemigrations

.PHONY: shell
shell:
	docker compose exec -it web python manage.py shell

.PHONY: psql
psql:
	docker compose exec -it db /bin/bash
# 	psql -U root -d stackup

.PHONY: sync-stripe-products
sync-stripe-products:
	docker compose exec -it web python manage.py sync_stripe_products

.PHONY: load
load:
	docker compose exec -it web python manage.py ${script}

.PHONY: update_invoice_pdfs
update_invoice_pdfs:
	docker compose exec -it web python manage.py fill_invoice_pdfs --batch 20
