# Ecommerce FastAPI backend

A Docker Compose development stack with FastAPI, PostgreSQL, SQLAlchemy 2, Alembic,
and an idempotent sample-data seed command.

## Start the stack

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, matching DATABASE_URL, and JWT_SECRET.
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
inventory, 10 customer login accounts, 2 admin accounts, and 25 orders distributed across placed, confirmed, processing, shipped,
delivered, cancelled, and refunded states.

The sample storefront is US-centric: addresses use US states and ZIP codes, phone
numbers use the `+1` country code, prices are representative USD amounts, standard
shipping is $8.99 (free at $100), and checkout uses a simplified 8.25% demonstration
tax rate. Real US sales tax should be calculated from the delivery jurisdiction.

## Useful commands

```bash
# Check service/database readiness
curl http://localhost:8000/ready

# List your orders (TOKEN comes from login; see Authentication below)
curl 'http://localhost:8000/api/v1/orders?page=1&page_size=10' -H "Authorization: Bearer $TOKEN"

# Filter shipped orders
curl 'http://localhost:8000/api/v1/orders?status=shipped' -H "Authorization: Bearer $TOKEN"

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

## Authentication and authorization

Set `JWT_SECRET` in `.env` before starting the updated stack. Generate a random
value with `openssl rand -hex 32`; do not use the demo account password as a signing
secret. `JWT_EXPIRY_MINUTES` defaults to 30. Rebuild with `docker compose up --build`
to apply migration `0002`, then run the seed command shown above.

The migration adds `accounts`, `roles`, `permissions`, `account_roles`, and
`role_permissions`. Accounts have globally unique lowercase login emails, Argon2
password hashes, an active flag, and an optional unique customer-profile link.
Customers and their historical orders remain unchanged. Accounts with customer
permissions need a customer-profile link; admin-only accounts do not.

The seed command adds accounts for `customer1@example.com` through
`customer10@example.com`, plus `admin1@example.com` and `admin2@example.com`.
All demo accounts initially use **`DemoPass123!`**. This shared password is only for
the development seed dataset. Each account stores its own salted hash.
The migration does not assign passwords or login accounts to other existing customers.

Seed reruns create missing records but preserve existing account passwords,
activation state, role assignments, and existing roles' permission mappings.
In particular, rerunning the seed does not restore a grant you removed from a role.
Deleted demo accounts or default roles will be recreated by the seed command.

Login accepts JSON:

```bash
# Customer login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"customer1@example.com","password":"DemoPass123!"}'

# Admin login
curl -X POST http://localhost:8000/api/v1/admin/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin1@example.com","password":"DemoPass123!"}'
```

Both return `access_token`, `token_type` (`bearer`), and `expires_in` (seconds).
Use the returned token in `Authorization: Bearer <token>`. In `/docs`, use the
Authorize button with the token. `GET /api/v1/auth/me` returns the current account,
role names, and effective permissions; hashes are never included in API responses.

JWTs identify accounts, rather than freezing their roles or permissions. Each
protected request checks the signature, required claims, expiration, issuer,
audience, current account state, and database grants. Changes to grants and
account activation take effect on the next request, including for existing tokens.
User login requires `user:access`; admin login requires `admin:access`.
An account with both capabilities can use either login endpoint; tokens from
both endpoints identify the same account and carry its combined access.

Missing/invalid tokens and invalid credentials return `401`; insufficient grants
return `403`. Another customer's order or address returns `404`. There is no
registration, password-reset, refresh-token, or per-token logout endpoint in this
version. Disabling an account blocks all of its existing tokens.

### API capabilities

Public catalog reads remain under `/api/v1/categories` and `/api/v1/products`;
inactive products are hidden. `/health` and `/ready` remain public.

| API | Required permission, in addition to the area's access permission |
| --- | --- |
| `GET /api/v1/customers/me` and `/customers/me/addresses` | `profile:read_own` |
| `PATCH /api/v1/customers/me`, `POST /customers/me/addresses`, `PATCH/DELETE /addresses/{id}` | `profile:write_own` |
| `POST /api/v1/orders` | `orders:create_own` |
| User order list/detail/number lookup, payments, shipments, and history | `orders:read_own` |
| `POST /api/v1/orders/{id}/cancel` | `orders:cancel_own` |
| Admin customer list/create/detail/update and address operations | `customers:manage` |
| Admin category/product reads, including inactive products | `catalog:read_all` |
| Admin category creation, product creation/update/deactivation | `catalog:write` |
| Admin inventory read/update | `inventory:manage` |
| Admin order list/detail/number lookup, payments, shipments, and history | `orders:read_all` |
| Admin order creation/cancellation/status changes, payment creation, shipment creation/update | `orders:manage` |
| Admin account list and activation/deactivation | `accounts:manage` |

User operations additionally require `user:access`; all admin operations require
`admin:access` and live under `/api/v1/admin`. Account activation uses
`PATCH /api/v1/admin/accounts/{id}` with `{"is_active": false}` or `true`.
Roles named `user` and `admin` are seeded with their respective grants, but the
API never uses those role names to decide access. Creating a customer profile
through the admin API does not automatically provision a login account.

**Client migration:** customer self-service now uses `/customers/me`. Privileged
writes have moved to the admin prefix; their old unprotected routes are removed.
User order creation no longer accepts `customer_id`; it comes from the account.
User order lists always filter to that account, including totals, regardless of
any submitted customer filter. Admin order creation still accepts `customer_id`.

```bash
# Set TOKEN to the access_token returned by login.
curl http://localhost:8000/api/v1/orders \
  -H "Authorization: Bearer $TOKEN"

curl -X POST http://localhost:8000/api/v1/orders \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"shipping_address_id":"ADDRESS_UUID","items":[{"product_id":"PRODUCT_UUID","quantity":1}]}'
```

### Managing roles and permissions in PostgreSQL

Roles and their grants are database-managed; there is no role-management API or UI
in this version. `account_roles` and `role_permissions` use composite primary keys,
so duplicate assignments are rejected. Their foreign keys prevent invalid links
and remove assignments when a role or permission is deleted.

For example, run this transaction against your development database to give a
customer account an additional read-only support role:

```sql
BEGIN;
INSERT INTO roles (id, name) VALUES (gen_random_uuid(), 'support_agent')
ON CONFLICT (name) DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r CROSS JOIN permissions p
WHERE r.name = 'support_agent'
  AND p.name IN ('admin:access', 'orders:read_all')
ON CONFLICT DO NOTHING;

INSERT INTO account_roles (account_id, role_id)
SELECT a.id, r.id
FROM accounts a CROSS JOIN roles r
WHERE a.email = 'customer1@example.com' AND r.name = 'support_agent'
ON CONFLICT DO NOTHING;
COMMIT;
```

The account retains its user role and can now use the admin login and read all
orders. It cannot change orders or manage accounts. Revoke a grant with:

```sql
DELETE FROM role_permissions
WHERE role_id = (SELECT id FROM roles WHERE name = 'support_agent')
  AND permission_id = (SELECT id FROM permissions WHERE name = 'orders:read_all');
```

The database controls assignments; application code defines and enforces each
permission's meaning and record ownership. `app/permissions.py` centralizes these
capabilities in `PermissionCode` and default seed role names in `BuiltinRole`.
Use enum members in Python code; their string values remain stable in the database
and API responses. Adding a new permission name alone
does not create a new API capability. Keep at least one active account with the
administrative grants you need; direct database changes can otherwise lock out
all administrators.

### Tests and migration rollback

Install development dependencies in a Python 3.12+ virtual environment:

```bash
python -m pip install -e '.[dev]'
```

Use a **disposable PostgreSQL instance**, never your application database.
For example, with PostgreSQL binaries on your PATH:

```bash
initdb -D /tmp/ecommerce-auth-tests -A trust --no-locale -E UTF8
pg_ctl -D /tmp/ecommerce-auth-tests -l /tmp/ecommerce-auth-tests.log \
  -o '-p 55439 -h 127.0.0.1 -k /tmp' start
python -m pytest -q
python -m ruff check .
git diff --check
pg_ctl -D /tmp/ecommerce-auth-tests stop
```

Tests default to the current OS username and port 55439. Alternatively, set
`TEST_DATABASE_URL` to a plain PostgreSQL SQLAlchemy URL for a disposable database
with schema-creation privileges. Tests override the application URL and signing
secret, migrate a unique schema for each test, and drop that schema afterward.
The suite covers login, invalid tokens, live permission changes, combined/custom
roles, ownership, admin operations, seed preservation, constraints, and migration
upgrade/downgrade.

`alembic downgrade 0001` removes all five authorization tables and permanently
loses account credentials and role/permission assignments; customer/order data is
preserved. Back up authorization data first if you need it. Re-upgrading recreates
empty tables, and the seed command restores only the demo accounts and default grants.
