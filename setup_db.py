"""
setup_db.py - Initialize the inventory SQLite database.
Run once before using the system: python setup_db.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "inventory.db")


def setup_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS inventory")
    cursor.execute("DROP TABLE IF EXISTS vendors")
    cursor.execute("DROP TABLE IF EXISTS processing_log")

    cursor.execute("""
        CREATE TABLE inventory (
            item        TEXT PRIMARY KEY,
            stock       INTEGER NOT NULL,
            unit_price  REAL,
            category    TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE vendors (
            name            TEXT PRIMARY KEY,
            trusted         INTEGER NOT NULL DEFAULT 1,
            payment_terms   INTEGER DEFAULT 30
        )
    """)

    cursor.execute("""
        CREATE TABLE processing_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number  TEXT,
            vendor          TEXT,
            amount          REAL,
            status          TEXT,
            flags           TEXT,
            reasoning       TEXT,
            processed_at    TEXT
        )
    """)

    # Inventory seed data
    inventory_items = [
        ("WidgetA",      15,  50.00,  "Components"),
        ("WidgetB",      10,  80.00,  "Components"),
        ("GadgetX",       5, 600.00,  "Electronics"),
        ("FakeItem",      0,  99.99,  "Unknown"),
    ]
    cursor.executemany(
        "INSERT INTO inventory VALUES (?, ?, ?, ?)", inventory_items
    )

    # Vendor seed data
    vendors = [
        ("Acme Supplies Co.",       1, 30),
        ("TechParts Ltd.",          1, 30),
        ("Shady Vendor Inc.",       0, 15),
        ("Premium Parts Corp.",     1, 30),
        ("Future Technologies Inc.",1, 45),
        ("Corrupt Data Corp.",      0, 15),
        ("Bulk Orders LLC",         1, 30),
        ("Enterprise Solutions Group", 1, 30),
    ]
    cursor.executemany(
        "INSERT INTO vendors VALUES (?, ?, ?)", vendors
    )

    conn.commit()
    conn.close()
    print(f"[DB] Inventory database initialized at: {DB_PATH}")


if __name__ == "__main__":
    setup_database()
