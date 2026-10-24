import json, os, re, sqlite3, sys, tempfile, unittest
from datetime import date, timedelta

TMP = tempfile.mkdtemp()
os.environ["MSMS_DB"] = os.path.join(TMP, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as msms  # noqa: E402


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        msms.app.config["TESTING"] = True

    def setUp(self):
        if os.path.exists(msms.DB_PATH):
            os.remove(msms.DB_PATH)
        msms.init_db()
        self.c = msms.app.test_client()

    def db(self):
        return msms.connect()

    def token(self, path="/login"):
        html = self.c.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def login(self, user="admin", pw="admin123"):
        return self.c.post("/login", data={"username": user, "password": pw, "csrf_token": self.token()})

    def post(self, url, data=None, page="/"):
        d = dict(data or {})
        d["csrf_token"] = self.token(page)
        return self.c.post(url, data=d, follow_redirects=False)

    def med(self, name):
        return self.db().execute("SELECT * FROM medicines WHERE name=?", (name,)).fetchone()

    def sell(self, items, **extra):
        data = {"items": json.dumps(items), "payment_mode": "Cash", "discount_pct": "0"}
        data.update(extra)
        return self.post("/billing", data, "/billing")


class AuthTests(Base):
    def test_login_required(self):
        for p in ("/", "/medicines", "/billing", "/reports/daily", "/suppliers"):
            r = self.c.get(p)
            self.assertEqual(r.status_code, 302)
            self.assertIn("/login", r.headers["Location"])

    def test_login_ok_and_bad(self):
        msms._failed.clear()
        r = self.c.post("/login", data={"username": "admin", "password": "nope", "csrf_token": self.token()})
        self.assertEqual(r.status_code, 401)
        r = self.login()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_password_hashed(self):
        h = self.db().execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
        self.assertNotIn("admin123", h)
        self.assertTrue(h.startswith(("scrypt:", "pbkdf2:")))

    def test_logout(self):
        self.login()
        self.post("/logout")
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_csrf_rejected(self):
        self.login()
        self.assertEqual(self.c.post("/medicines/1/delete").status_code, 400)

    def test_pharmacist_cannot_delete(self):
        self.login("pharmacist", "pharma123")
        self.post("/medicines/1/delete")
        self.assertIsNotNone(self.db().execute("SELECT 1 FROM medicines WHERE id=1").fetchone())
        self.assertEqual(self.c.get("/users").status_code, 302)

    def test_open_redirect_blocked(self):
        r = self.c.post("/login?next=//evil.com", data={"username": "admin", "password": "admin123", "csrf_token": self.token()})
        self.assertNotIn("evil.com", r.headers["Location"])

    def test_change_password(self):
        self.login()
        r = self.post("/profile", {"name": "Boss", "current_password": "admin123", "new_password": "newpass99",
                                   "confirm_password": "newpass99"}, "/profile")
        self.assertEqual(r.status_code, 302)
        self.post("/logout")
        self.assertEqual(self.login("admin", "newpass99").status_code, 302)


class CrudTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_medicine_crud(self):
        exp = (date.today() + timedelta(days=200)).isoformat()
        form = {"name": "TestMed 10mg", "batch_no": "T1", "category": "General", "price": "12.50", "quantity": "40",
                "reorder_level": "10", "expiry_date": exp, "supplier_id": "1"}
        self.assertEqual(self.post("/medicines/add", form, "/medicines/add").status_code, 302)
        m = self.med("TestMed 10mg")
        self.assertEqual(m["quantity"], 40)
        # duplicate rejected
        r = self.post("/medicines/add", form, "/medicines/add")
        self.assertEqual(r.status_code, 200)
        # edit
        form["price"] = "15.00"
        self.post(f"/medicines/{m['id']}/edit", form, f"/medicines/{m['id']}/edit")
        self.assertEqual(self.med("TestMed 10mg")["price"], 15.0)
        # search
        self.assertIn("TestMed 10mg", self.c.get("/medicines?q=TestMed").get_data(as_text=True))
        # delete
        self.post(f"/medicines/{m['id']}/delete")
        self.assertIsNone(self.med("TestMed 10mg"))

    def test_medicine_validation(self):
        r = self.post("/medicines/add", {"name": "", "batch_no": "", "price": "-1", "quantity": "x", "expiry_date": "bad"}, "/medicines/add")
        self.assertEqual(r.status_code, 200)

    def test_supplier_crud(self):
        self.post("/suppliers/add", {"name": "New Sup", "contact": "9876543210", "email": "a@b.co", "address": "X"}, "/suppliers/add")
        s = self.db().execute("SELECT * FROM suppliers WHERE name='New Sup'").fetchone()
        self.assertIsNotNone(s)
        self.post(f"/suppliers/{s['id']}/edit", {"name": "New Sup 2", "contact": "9876543210"}, f"/suppliers/{s['id']}/edit")
        self.assertEqual(self.db().execute("SELECT name FROM suppliers WHERE id=?", (s["id"],)).fetchone()[0], "New Sup 2")
        self.post(f"/suppliers/{s['id']}/delete")
        self.assertIsNone(self.db().execute("SELECT 1 FROM suppliers WHERE id=?", (s["id"],)).fetchone())

    def test_customer_and_prescription(self):
        self.post("/customers/add", {"name": "Zed", "contact": "9999988888"}, "/customers/add")
        c = self.db().execute("SELECT id FROM customers WHERE name='Zed'").fetchone()
        self.post("/prescriptions", {"customer_id": c["id"], "doctor_name": "Dr X", "details": "Rx"}, "/prescriptions")
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM prescriptions WHERE customer_id=?", (c["id"],)).fetchone()[0], 1)

    def test_restock(self):
        m = self.med("Telmisartan 40mg")
        self.assertEqual(m["quantity"], 0)
        self.post(f"/inventory/{m['id']}/restock", {"qty": "30"}, "/inventory")
        self.assertEqual(self.med("Telmisartan 40mg")["quantity"], 30)
        self.post(f"/inventory/{m['id']}/restock", {"qty": "-5"}, "/inventory")
        self.assertEqual(self.med("Telmisartan 40mg")["quantity"], 30)


class BillingTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_calculation_and_stock(self):
        a, b = self.med("Paracetamol 500mg"), self.med("Ibuprofen 400mg")   # 18.50, 32.00
        r = self.sell([{"id": a["id"], "qty": 3}, {"id": b["id"], "qty": 2}], discount_pct="10")
        self.assertEqual(r.status_code, 302)
        sale = self.db().execute("SELECT * FROM sales ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(sale["subtotal"], 119.50)          # 55.5 + 64
        self.assertEqual(sale["discount_amt"], 11.95)
        self.assertEqual(sale["total_amount"], 107.55)
        self.assertEqual(self.med("Paracetamol 500mg")["quantity"], a["quantity"] - 3)
        self.assertEqual(self.med("Ibuprofen 400mg")["quantity"], b["quantity"] - 2)
        page = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("₹107.55", page)
        self.assertIn(sale["invoice_no"], page)

    def test_rounding_half_up(self):
        self.post("/medicines/add", {"name": "Penny", "batch_no": "P", "price": "0.05", "quantity": "100",
                                     "expiry_date": (date.today() + timedelta(days=90)).isoformat()}, "/medicines/add")
        m = self.med("Penny")
        self.sell([{"id": m["id"], "qty": 3}], discount_pct="50")   # 0.15 * 50% = 0.075 -> 0.08
        s = self.db().execute("SELECT * FROM sales ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((s["subtotal"], s["discount_amt"], s["total_amount"]), (0.15, 0.08, 0.07))

    def test_expired_cannot_be_sold(self):
        e = self.med("Cough Syrup 100ml")
        before = self.db().execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        r = self.sell([{"id": e["id"], "qty": 1}])
        self.assertEqual(r.status_code, 200)
        self.assertIn("expired and cannot be sold", r.get_data(as_text=True))
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM sales").fetchone()[0], before)
        self.assertEqual(self.med("Cough Syrup 100ml")["quantity"], e["quantity"])

    def test_expiry_today_is_blocked(self):
        self.post("/medicines/add", {"name": "TodayMed", "batch_no": "X", "price": "10", "quantity": "5",
                                     "expiry_date": date.today().isoformat()}, "/medicines/add")
        m = self.med("TodayMed")
        r = self.sell([{"id": m["id"], "qty": 1}])
        self.assertIn("expired", r.get_data(as_text=True))

    def test_insufficient_stock_and_atomic(self):
        a, t = self.med("Paracetamol 500mg"), self.med("Diclofenac 50mg")   # Diclofenac qty 8
        r = self.sell([{"id": a["id"], "qty": 2}, {"id": t["id"], "qty": 9}])
        self.assertIn("Not enough stock", r.get_data(as_text=True))
        self.assertEqual(self.med("Paracetamol 500mg")["quantity"], a["quantity"])   # nothing deducted

    def test_invalid_inputs(self):
        a = self.med("Paracetamol 500mg")
        for items, disc in (([], "0"), ([{"id": a["id"], "qty": 0}], "0"), ([{"id": a["id"], "qty": 1}], "101"),
                            ([{"id": 9999, "qty": 1}], "0")):
            r = self.sell(items, discount_pct=disc)
            self.assertEqual(r.status_code, 200)

    def test_client_price_ignored(self):
        a = self.med("Paracetamol 500mg")
        self.sell([{"id": a["id"], "qty": 1, "price": 0.01}])
        s = self.db().execute("SELECT subtotal FROM sales ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(s[0], 18.5)

    def test_new_customer_and_rx(self):
        a = self.med("Paracetamol 500mg")
        self.sell([{"id": a["id"], "qty": 1}], customer_id="new", new_name="Walker", new_contact="9000000000",
                  rx_doctor="Dr Q", rx_details="PCM")
        s = self.db().execute("SELECT * FROM sales ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(s["customer_name"], "Walker")
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM prescriptions WHERE sale_id=?", (s["id"],)).fetchone()[0], 1)

    def test_invoice_survives_medicine_delete(self):
        a = self.med("Paracetamol 500mg")
        self.sell([{"id": a["id"], "qty": 1}])
        sid = self.db().execute("SELECT MAX(id) FROM sales").fetchone()[0]
        self.post(f"/medicines/{a['id']}/delete")
        self.assertIn("Paracetamol 500mg", self.c.get(f"/sales/{sid}").get_data(as_text=True))

    def test_seed_data_consistent(self):
        for s in self.db().execute("SELECT * FROM sales"):
            tot = self.db().execute("SELECT ROUND(SUM(subtotal),2) FROM sale_items WHERE sale_id=?", (s["id"],)).fetchone()[0]
            self.assertAlmostEqual(tot, s["subtotal"], places=2)
            self.assertAlmostEqual(s["subtotal"] - s["discount_amt"], s["total_amount"], places=2)


class AlertsReportsTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_alert_types_and_autoclear(self):
        self.c.get("/alerts")
        types = {r[0] for r in self.db().execute("SELECT DISTINCT type FROM stock_alerts")}
        self.assertEqual(types, {"low_stock", "out_of_stock", "expiring_soon", "expired"})
        m = self.med("Diclofenac 50mg")
        self.post(f"/inventory/{m['id']}/restock", {"qty": "100"}, "/inventory")
        self.c.get("/alerts")
        self.assertIsNone(self.db().execute("SELECT 1 FROM stock_alerts WHERE medicine_id=? AND type='low_stock'", (m["id"],)).fetchone())

    def test_reports(self):
        for u in ("/reports/daily", "/reports/monthly", "/reports/stock", "/reports/daily?date=2000-01-01",
                  "/reports/monthly?month=2024-02"):
            self.assertEqual(self.c.get(u).status_code, 200, u)
        csv = self.c.get("/reports/monthly?format=csv")
        self.assertEqual(csv.mimetype, "text/csv")
        # monthly net equals DB sum
        key = date.today().strftime("%Y-%m")
        want = self.db().execute("SELECT COALESCE(SUM(total_amount),0) FROM sales WHERE strftime('%Y-%m',sale_date)=?", (key,)).fetchone()[0]
        got = sum(float(l.split(",")[-1]) for l in csv.get_data(as_text=True).strip().splitlines()[1:])
        self.assertAlmostEqual(want, got, places=1)

    def test_availability_public(self):
        r = self.client_anon().get("/availability?q=Paracetamol")
        self.assertEqual(r.status_code, 200)
        self.assertIn("In stock", r.get_data(as_text=True))
        self.assertIn("Not available", self.client_anon().get("/availability?q=Cough").get_data(as_text=True))

    def client_anon(self):
        return msms.app.test_client()


class CrawlTest(Base):
    def test_every_internal_link_works(self):
        self.login()
        seen, queue = set(), ["/", "/medicines/1/edit", "/sales/1", "/suppliers/1", "/customers/1", "/medicines/add",
                              "/suppliers/add", "/customers/add", "/users", "/availability"]
        while queue:
            url = queue.pop()
            if url in seen:
                continue
            seen.add(url)
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, f"{url} -> {r.status_code}")
            for href in re.findall(r'href="(/[^"#]*)"', r.get_data(as_text=True)):
                if href.startswith(("/static", "/login")) or "format=csv" in href or "date=" in href or "month=" in href or href in seen:
                    continue
                queue.append(href.replace("&amp;", "&"))
        self.assertGreater(len(seen), 25)

    def test_404(self):
        self.login()
        self.assertEqual(self.c.get("/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
