# Medical Store Management System

A web-based pharmacy management application for **MediCare Pharmacy** (Vesu, Surat), built from the
project presentation *"Medical Store Management System"* (Rana Nidhi, Asha M. Tarsadia Institute of
Computer Science & Technology).

**Stack:** HTML5 · CSS3 · JavaScript · Bootstrap 5 · Python Flask · SQLite

> 📖 **Architecture & Deep Dive:** For an in-depth breakdown of the database schema, ACID billing transactions, security protocols, and state machines, see [TECHNICAL_README.md](TECHNICAL_README.md).

## Features (mapped to the presentation)

| Presentation item | Where in the app |
|---|---|
| **FR-1** Medicine & Inventory Management (name, batch, quantity, price, expiry) | *Medicines*, *Inventory* (restock, low-stock) |
| **FR-2** Supplier Management (name, contact, address, medicines supplied) | *Suppliers* (detail page lists each supplier's medicines) |
| **FR-3** Sales & Billing (select customer → add medicines → payment → bill → stock update) | *New Bill*, *Sales History*, printable *Invoice* |
| **FR-4** Customer & Prescription Management | *Customers*, *Prescriptions* (also saved while billing) |
| Use case: *Medicine Availability Search* (Customer actor, no login) | `/availability`, linked from the login page |
| Use case: *View Reports* (Stock Report, Sales Report) | *Reports*: Daily, Monthly, Stock (print + CSV) |
| Class diagram: `StockAlert` (low stock / expiry alerts) | *Alerts* page + bell badge, generated automatically |
| Class diagram: `User` (login, logout, updateProfile) | Login, Logout, *My Profile*, *Users* (admin) |
| Activity diagram: Login → Dashboard → modules → Logout | The sidebar navigation follows this flow |

Also included: dashboard (total medicines, stock info, today's and monthly sales, 7-day chart, top
sellers), expiry management (expired / expiring in 30-60-90 days), and a responsive UI for desktop
and mobile.

### Business rules
* **Expired medicines cannot be sold.** A batch is treated as expired from its expiry date onward. It is
  blocked in the billing screen *and* re-checked on the server inside the sale transaction.
* Stock can never go negative; a bill is saved completely or not at all.
* Prices are always taken from the database (never from the browser). Money is calculated with
  `Decimal`, rounded half-up to 2 decimals. `Total = Subtotal − Discount%`.
* Deleting a medicine or customer keeps old invoices intact (names and prices are stored on the bill).
* Only **admin** can delete records; **pharmacist** can do everything else.

## Installation

Requires **Python 3.9+**.

```bash
# 1. open a terminal in this folder (in VS Code: Terminal > New Terminal)
python -m venv venv

# 2. activate it
#   Windows (PowerShell):  venv\Scripts\Activate.ps1
#   Windows (cmd):         venv\Scripts\activate.bat
#   macOS / Linux:         source venv/bin/activate

# 3. install dependencies
pip install -r requirements.txt

# 4. run
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

The first run automatically creates `instance/medical_store.db` and loads the sample data
(27 medicines including low-stock, expiring and expired ones, 5 suppliers, 6 customers and about
35 days of sales). To reset everything, stop the app and delete `instance/medical_store.db`.

### Default logins

| Role | Username | Password |
|---|---|---|
| Admin (shop owner) | `admin` | `admin123` |
| Pharmacist | `pharmacist` | `pharma123` |

Change the passwords from **My Profile** after the first login. Passwords are stored as salted hashes
(Werkzeug `scrypt`/`pbkdf2`), never in plain text.

### Internet note
Bootstrap 5 and Bootstrap Icons are loaded from the jsDelivr CDN, so the browser needs internet access.
For fully offline use, download `bootstrap.min.css`, `bootstrap.bundle.min.js` and `bootstrap-icons`
into `static/vendor/` and point the `<link>`/`<script>` tags in `templates/base.html`,
`templates/plain.html` and `templates/login.html` at them.

## Project structure

```
medical_store/
├── app.py              Flask application: routes, auth, billing logic, reports
├── schema.sql          SQLite tables (users, suppliers, medicines, customers,
│                       sales, sale_items, prescriptions, stock_alerts)
├── seed_data.py        Sample data loaded on first run
├── requirements.txt
├── README.md
├── instance/           SQLite database + secret key are created here at runtime
├── static/
│   ├── css/style.css   Custom theme + print styles
│   └── js/app.js       Confirm dialogs, print buttons
├── templates/          Jinja2 pages (base layout, one template per screen)
└── tests/test_app.py   Automated tests
```

## Running the tests

```bash
python -m unittest discover -s tests -v
```

The suite uses a temporary database. It covers login, roles and CSRF, CRUD, billing arithmetic and
rounding, stock deduction, expired-medicine blocking, all-or-nothing sales, alerts, reports, and a
crawl of every page and internal link.

## Configuration (optional environment variables)

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Session signing key | auto-generated in `instance/secret.key` |
| `PORT` / `HOST` | Server port / interface | `5000` / `127.0.0.1` |
| `MSMS_DB` | Path of the SQLite file | `instance/medical_store.db` |
| `FLASK_DEBUG=1` | Auto-reload while developing | off |

The store name, address and phone shown on invoices are in the `STORE` dictionary at the top of
`app.py` (taken from the client's contract letter). The expiry-alert window is `EXPIRY_ALERT_DAYS`.

## Notes
* The presentation's deployment diagram shows MySQL; this build uses **SQLite** as specified in the
  project brief. All SQL is standard, so moving to MySQL later only needs a different connection layer.
* For real deployment use a production server (for example `waitress` or `gunicorn`) behind HTTPS,
  not the Flask development server.
