"""Sample data for the Medical Store Management System.

All dates are relative to "today" so the demo always shows a realistic mix of
healthy, low-stock, expiring-soon and expired medicines.
"""
import random
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from werkzeug.security import generate_password_hash

DEFAULT_ADMIN = ("Store Admin", "admin", "admin123", "admin")
DEFAULT_PHARMACIST = ("Pharmacist", "pharmacist", "pharma123", "pharmacist")

SUPPLIERS = [
    ("Sun Pharma Distributors", "+91 98250 11101", "orders@sunpharma-dist.example", "Ring Road, Surat, Gujarat 395002"),
    ("Cipla Healthcare Supplies", "+91 98250 22202", "sales@ciplahealth.example", "Udhna Magdalla Road, Surat, Gujarat 395007"),
    ("Zydus Medico Traders", "+91 98250 33303", "contact@zydusmedico.example", "CG Road, Ahmedabad, Gujarat 380009"),
    ("Apollo Wholesale Pharma", "+91 98250 44404", "wholesale@apollopharma.example", "Alkapuri, Vadodara, Gujarat 390007"),
    ("Mehta Surgical & Generic", "+91 98250 55505", "mehta.generic@example.com", "Varachha Road, Surat, Gujarat 395006"),
]

# name, batch, category, price, qty, reorder, expiry offset (days from today), supplier idx (1-based)
MEDICINES = [
    ("Paracetamol 500mg", "PCM-2401", "Analgesic", 18.50, 220, 50, 420, 1),
    ("Ibuprofen 400mg", "IBU-2402", "Analgesic", 32.00, 140, 40, 300, 1),
    ("Diclofenac 50mg", "DIC-2403", "Analgesic", 28.75, 8, 25, 210, 2),
    ("Amoxicillin 500mg", "AMX-2404", "Antibiotic", 96.00, 75, 30, 365, 2),
    ("Azithromycin 500mg", "AZI-2405", "Antibiotic", 118.00, 12, 20, 18, 2),
    ("Ciprofloxacin 500mg", "CIP-2406", "Antibiotic", 84.50, 60, 25, 540, 3),
    ("Cetirizine 10mg", "CET-2407", "Antihistamine", 22.00, 190, 40, 480, 3),
    ("Loratadine 10mg", "LOR-2408", "Antihistamine", 41.00, 5, 20, 25, 3),
    ("Omeprazole 20mg", "OME-2409", "Antacid", 54.00, 110, 30, 330, 4),
    ("Pantoprazole 40mg", "PAN-2410", "Antacid", 72.25, 95, 30, 400, 4),
    ("Digene Antacid Gel 200ml", "DIG-2411", "Antacid", 135.00, 38, 15, 250, 4),
    ("Metformin 500mg", "MET-2412", "Antidiabetic", 35.00, 260, 60, 600, 1),
    ("Glimepiride 2mg", "GLI-2413", "Antidiabetic", 88.00, 18, 30, 120, 1),
    ("Amlodipine 5mg", "AML-2414", "Cardiac", 29.00, 170, 40, 450, 5),
    ("Atorvastatin 10mg", "ATO-2415", "Cardiac", 112.00, 90, 30, 380, 5),
    ("Telmisartan 40mg", "TEL-2416", "Cardiac", 96.50, 0, 25, 280, 5),
    ("Vitamin C 500mg", "VTC-2417", "Vitamins", 65.00, 150, 40, 700, 3),
    ("Vitamin D3 60K IU", "VTD-2418", "Vitamins", 145.00, 45, 20, 28, 3),
    ("Multivitamin Capsules", "MVT-2419", "Vitamins", 210.00, 55, 20, 520, 4),
    ("ORS Powder Sachet", "ORS-2420", "General", 21.00, 300, 80, 640, 2),
    ("Cough Syrup 100ml", "CGS-2421", "Cough & Cold", 78.00, 7, 20, -45, 2),
    ("Betadine Ointment 20g", "BET-2422", "First Aid", 92.00, 33, 15, -12, 4),
    ("Crocin Cold & Flu", "CRC-2423", "Cough & Cold", 56.00, 4, 25, -3, 1),
    ("Salbutamol Inhaler", "SAL-2424", "Respiratory", 168.00, 26, 10, 22, 5),
    ("Dettol Antiseptic 250ml", "DET-2425", "First Aid", 118.00, 62, 20, 800, 5),
    ("Surgical Mask (Box of 50)", "MSK-2426", "Surgical", 150.00, 85, 25, 900, 5),
    ("Digital Thermometer", "THR-2427", "Surgical", 210.00, 20, 8, 1400, 5),
]

CUSTOMERS = [
    ("Rahul Desai", "+91 90000 10001", "Vesu, Surat"),
    ("Priya Patel", "+91 90000 10002", "Adajan, Surat"),
    ("Amit Shah", "+91 90000 10003", "Piplod, Surat"),
    ("Neha Joshi", "+91 90000 10004", "City Light, Surat"),
    ("Kiran Mehta", "+91 90000 10005", "Althan, Surat"),
    ("Sunita Verma", "+91 90000 10006", "Dumas Road, Surat"),
]

PRESCRIPTIONS = [
    (1, "Dr. A. Trivedi", "Amoxicillin 500mg - 1 tab x 3/day for 5 days; Paracetamol 500mg SOS", 3),
    (3, "Dr. S. Kapadia", "Metformin 500mg - 1 tab twice daily after meals", 10),
    (5, "Dr. N. Bhatt", "Amlodipine 5mg - 1 tab morning; Atorvastatin 10mg - 1 tab night", 6),
]


def money(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def seed(db):
    """Populate an empty, freshly-created database."""
    today = date.today()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for name, username, pw, role in (DEFAULT_ADMIN, DEFAULT_PHARMACIST):
        db.execute(
            "INSERT INTO users (name, username, password_hash, role) VALUES (?,?,?,?)",
            (name, username, generate_password_hash(pw), role),
        )

    db.executemany("INSERT INTO suppliers (name, contact, email, address) VALUES (?,?,?,?)", SUPPLIERS)

    for n, b, c, p, q, r, off, s in MEDICINES:
        db.execute(
            "INSERT INTO medicines (name,batch_no,category,price,quantity,reorder_level,expiry_date,supplier_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (n, b, c, p, q, r, (today + timedelta(days=off)).isoformat(), s),
        )

    db.executemany("INSERT INTO customers (name, contact, address) VALUES (?,?,?)", CUSTOMERS)

    # Historical sales over the last ~35 days (only from healthy, in-date medicines)
    rng = random.Random(42)
    sellable = [m for m in db.execute(
        "SELECT id,name,batch_no,price FROM medicines WHERE expiry_date > ? AND quantity > 20",
        ((today + timedelta(days=60)).isoformat(),)).fetchall()]
    modes = ["Cash", "UPI", "Card"]
    inv_counter = 0
    for days_ago in range(35, -1, -1):
        for _ in range(rng.randint(1, 4)):
            d = today - timedelta(days=days_ago)
            ts = datetime(d.year, d.month, d.day, rng.randint(9, 20), rng.randint(0, 59), rng.randint(0, 59))
            if ts > datetime.now():
                continue
            items = rng.sample(sellable, rng.randint(1, 4))
            lines, subtotal = [], 0.0
            for m in items:
                qty = rng.randint(1, 4)
                sub = money(m["price"] * qty)
                subtotal = money(subtotal + sub)
                lines.append((m, qty, sub))
            disc_pct = rng.choice([0, 0, 0, 5, 10])
            disc_amt = money(subtotal * disc_pct / 100)
            total = money(subtotal - disc_amt)
            cust = rng.choice([None, None, 1, 2, 3, 4, 5, 6])
            cname = "Walk-in Customer"
            if cust:
                cname = CUSTOMERS[cust - 1][0]
            inv_counter += 1
            cur = db.execute(
                "INSERT INTO sales (invoice_no,sale_date,customer_id,customer_name,subtotal,discount_pct,"
                "discount_amt,total_amount,payment_mode,user_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"TMP{inv_counter}", ts.strftime("%Y-%m-%d %H:%M:%S"), cust, cname, subtotal, disc_pct,
                 disc_amt, total, rng.choice(modes), 1),
            )
            sid = cur.lastrowid
            db.execute("UPDATE sales SET invoice_no=? WHERE id=?", (f"INV-{sid:05d}", sid))
            for m, qty, sub in lines:
                db.execute(
                    "INSERT INTO sale_items (sale_id,medicine_id,medicine_name,batch_no,quantity,price,subtotal)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (sid, m["id"], m["name"], m["batch_no"], qty, m["price"], sub),
                )

    for cust_id, doctor, details, ago in PRESCRIPTIONS:
        db.execute(
            "INSERT INTO prescriptions (customer_id,doctor_name,details,issued_on) VALUES (?,?,?,?)",
            (cust_id, doctor, details, (today - timedelta(days=ago)).isoformat()),
        )
