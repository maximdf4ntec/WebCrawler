"""Analyze the crawl database and print statistics."""

import sqlite3


def analyze(db_path: str, label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f" CRAWL ANALYSIS: {label}")
    print(f"{'=' * 60}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # State counts
    print("\n--- CRAWL STATE SUMMARY ---")
    cur.execute(
        "SELECT crawl_state, COUNT(*) as cnt FROM url_records GROUP BY crawl_state ORDER BY cnt DESC"
    )
    for r in cur.fetchall():
        print(f"  {r['crawl_state']:20s} {r['cnt']}")

    # Error breakdown
    print("\n--- ERROR BREAKDOWN (failure_reason) ---")
    cur.execute(
        "SELECT failure_reason, COUNT(*) as cnt FROM url_records WHERE failure_reason IS NOT NULL GROUP BY failure_reason ORDER BY cnt DESC"
    )
    for r in cur.fetchall():
        print(f"  {r['failure_reason']:45s} {r['cnt']}")

    # Content type breakdown
    print("\n--- CONTENT TYPE BREAKDOWN ---")
    cur.execute(
        "SELECT content_type, COUNT(*) as cnt FROM url_records WHERE content_type IS NOT NULL GROUP BY content_type ORDER BY cnt DESC"
    )
    for r in cur.fetchall():
        print(f"  {r['content_type']:40s} {r['cnt']}")

    # HTTP status code inference from failure reasons
    print("\n--- HTTP STATUS CODE COUNTERS (inferred) ---")
    status_map = {
        "200 (success)": "crawl_state = 'Completed'",
        "308 (redirect)": "failure_reason LIKE '%308%'",
        "403 (forbidden)": "failure_reason = 'blocked'",
        "404 (not found)": "failure_reason = 'not found'",
        "500 (server error)": "failure_reason = 'server error'",
    }
    for label_s, condition in status_map.items():
        cur.execute(f"SELECT COUNT(*) FROM url_records WHERE {condition}")
        count = cur.fetchone()[0]
        if count > 0:
            print(f"  {label_s:30s} {count}")

    # Sample failed URLs
    print("\n--- SAMPLE FAILED URLs (first 10) ---")
    cur.execute(
        "SELECT normalized_url, failure_reason FROM url_records WHERE crawl_state = 'Terminal_Failed' LIMIT 10"
    )
    for r in cur.fetchall():
        print(f"  {r['normalized_url']}")
        print(f"    Reason: {r['failure_reason']}")

    # Depth distribution
    print("\n--- DEPTH DISTRIBUTION ---")
    cur.execute(
        "SELECT crawl_depth, COUNT(*) as cnt FROM url_records GROUP BY crawl_depth ORDER BY crawl_depth"
    )
    for r in cur.fetchall():
        print(f"  Depth {r['crawl_depth']}: {r['cnt']} URLs")

    conn.close()


if __name__ == "__main__":
    import os

    if os.path.exists("secupi_crawl.db"):
        analyze("secupi_crawl.db", "secupi.com")
    if os.path.exists("quotes_crawl.db"):
        analyze("quotes_crawl.db", "quotes.toscrape.com")
