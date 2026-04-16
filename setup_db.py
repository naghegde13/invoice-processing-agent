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

    # Drop existing tables for idempotency: allows safe re-runs during development/testing
    # Ensures fresh state without requiring manual DB deletion
    cursor.execute("DROP TABLE IF EXISTS inventory")
    cursor.execute("DROP TABLE IF EXISTS vendors")
    cursor.execute("DROP TABLE IF EXISTS processing_log")

    # Inventory schema: items with stock levels, unit pricing, categorization
    cursor.execute("""
        CREATE TABLE inventory (
            item        TEXT PRIMARY KEY,
            stock       INTEGER NOT NULL,
            unit_price  REAL,
            category    TEXT
        )
    """)

    # Vendors schema: trust tier (trusted=0/1), payment terms for business rules
    # Note: trusted stored as INTEGER (0/1) because SQLite lacks native BOOLEAN type
    cursor.execute("""
        CREATE TABLE vendors (
            name            TEXT PRIMARY KEY,
            trusted         INTEGER NOT NULL DEFAULT 1,
            payment_terms   INTEGER DEFAULT 30
        )
    """)

    # Processing log: audit trail for all invoices processed (paid, rejected, errors)
    # Flags stored as JSON string for flexible validation error tracking
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

    # Seed inventory: includes valid items and FakeItem (stock=0) for testing OUT_OF_STOCK path
    inventory_items = [
        ("WidgetA",      15,  50.00,  "Components"),
        ("WidgetB",      10,  80.00,  "Components"),
        ("GadgetX",       5, 600.00,  "Electronics"),
        ("FakeItem",      0,  99.99,  "Unknown"),  # Zero stock for testing error conditions
    ]
    cursor.executemany(
        "INSERT INTO inventory VALUES (?, ?, ?, ?)", inventory_items
    )

    # Seed vendors: mix of trusted (1) and untrusted (0) for testing fraud/approval logic
    # Untrusted vendors should raise risk scores and potentially trigger flags
    vendors = [
        ("Acme Supplies Co.",       1, 30),
        ("TechParts Ltd.",          1, 30),
        ("Shady Vendor Inc.",       0, 15),  # Untrusted vendor for testing fraud detection
        ("Premium Parts Corp.",     1, 30),
        ("Future Technologies Inc.",1, 45),
        ("Corrupt Data Corp.",      0, 15),  # Untrusted vendor for testing risk scoring
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
