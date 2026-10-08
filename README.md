# Ecommerce FastAPI backend

A Docker Compose development stack with FastAPI, PostgreSQL, SQLAlchemy 2, Alembic,
and an idempotent sample-data seed command.

## Start the stack

```bash
cp .env.example .env
# Edit .env and fill POSTGRES_PASSWORD and DATABASE_URL with the same password.
docker compose up --build
```

Generate a suitable local password with `openssl rand -hex 24`. Keep `.env` local: it is
excluded from Git and from the Docker build context. Compose deliberately has no database
credential fallback and will stop with a clear error if required values are missing.

Compose waits for PostgreSQL to become healthy, runs all Alembic migrations, and then
starts the API at <http://localhost:8000>. Interactive API docs are available at
<http://localhost:8000/docs>.

## Seed sample data

In another terminal, while the stack is running:

```bash
docker compose exec api python -m app.scripts.seed
```

The command is safe to rerun. It ensures 10 customers, 5 categories, 30 products,
inventory, and 25 orders distributed across placed, confirmed, processing, shipped,
delivered, cancelled, and refunded states.

The sample storefront is US-centric: addresses use US states and ZIP codes, phone
numbers use the `+1` country code, prices are representative USD amounts, standard
shipping is $8.99 (free at $100), and checkout uses a simplified 8.25% demonstration
tax rate. Real US sales tax should be calculated from the delivery jurisdiction.

## Useful commands

```bash
# Check service/database readiness
curl http://localhost:8000/ready

# List orders
curl 'http://localhost:8000/api/v1/orders?page=1&page_size=10'

# Filter shipped orders
curl 'http://localhost:8000/api/v1/orders?status=shipped'

# Stop containers but preserve database data
docker compose down

# Remove containers and the local database volume
docker compose down --volumes
```

PostgreSQL is published only on `127.0.0.1:5433` by default. For pgAdmin, connect to
`127.0.0.1`, port `5433`, using the database, username, and password from your `.env`.
Containers connect internally on `db:5432`.

Never commit `.env`, private keys, exported credentials, or real values in `.env.example`.
For deployed environments, inject `DATABASE_URL` and the PostgreSQL settings through the
deployment platform's secret manager rather than copying the local `.env` file.

## Schema and API

The tables are `customers`, `addresses`, `categories`, `products`, `inventory`,
`orders`, `order_items`, `order_status_history`, `payments`, and `shipments`.

See `/docs` for the complete OpenAPI contract. All business endpoints are under
`/api/v1`; health endpoints are `/health` and `/ready`.
