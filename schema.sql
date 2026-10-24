-- Medical Store Management System - SQLite schema
-- Tables follow the class diagram in the project presentation:
-- User, Supplier, Medicine, Customer, Sale, SaleItem, StockAlert (+ Prescription for FR-4)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'pharmacist' CHECK (role IN ('admin','pharmacist')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS suppliers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    contact    TEXT NOT NULL,
    email      TEXT,
    address    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS medicines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    batch_no      TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'General',
    price         REAL NOT NULL CHECK (price >= 0),
    quantity      INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    reorder_level INTEGER NOT NULL DEFAULT 10 CHECK (reorder_level >= 0),
    expiry_date   TEXT NOT NULL,                       -- ISO date YYYY-MM-DD
    supplier_id   INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (name, batch_no)
);
CREATE INDEX IF NOT EXISTS idx_medicines_name   ON medicines(name);
CREATE INDEX IF NOT EXISTS idx_medicines_expiry ON medicines(expiry_date);

CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    contact    TEXT,
    address    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sales (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no   TEXT UNIQUE,
    sale_date    TEXT NOT NULL,                        -- 'YYYY-MM-DD HH:MM:SS'
    customer_id  INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    customer_name TEXT NOT NULL DEFAULT 'Walk-in Customer',
    subtotal     REAL NOT NULL,
    discount_pct REAL NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct <= 100),
    discount_amt REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL,
    payment_mode TEXT NOT NULL DEFAULT 'Cash',
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);

CREATE TABLE IF NOT EXISTS sale_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id       INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    medicine_id   INTEGER REFERENCES medicines(id) ON DELETE SET NULL,
    medicine_name TEXT NOT NULL,
    batch_no      TEXT,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    price         REAL NOT NULL,
    subtotal      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_sale ON sale_items(sale_id);

CREATE TABLE IF NOT EXISTS prescriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    sale_id     INTEGER REFERENCES sales(id) ON DELETE SET NULL,
    doctor_name TEXT NOT NULL,
    details     TEXT NOT NULL,
    issued_on   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS stock_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id) ON DELETE CASCADE,
    type        TEXT NOT NULL CHECK (type IN ('low_stock','out_of_stock','expiring_soon','expired')),
    message     TEXT NOT NULL,
    alert_date  TEXT NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (medicine_id, type)
);
