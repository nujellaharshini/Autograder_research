import json, sqlite3
from pathlib import Path

JSON_PATH = Path("/Users/harshininujella/Desktop/School_work/Research:EE-021/Research_feedback_generation/Autograder_research/local_autograder/tools/results_all.json")
DB_PATH   = "tpch.sqlite"

# Load JSON
data = json.loads(JSON_PATH.read_text())

# Connect + create tables
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS submissions (
  submission_id TEXT PRIMARY KEY,
  status        TEXT
);
CREATE TABLE IF NOT EXISTS results_filtered (
  submission_id TEXT,
  name          TEXT,   -- failed test name
  status        TEXT,   -- "failed" or "error"
  output        TEXT,   -- test output / error message
  PRIMARY KEY (submission_id, name),
  FOREIGN KEY (submission_id) REFERENCES submissions(submission_id)
);
""")

rows_failed = []
rows_submissions = []

for submission_id, payload in data.items():
    tests = payload.get("tests", [])
    failed_tests = []
    for t in tests:
      if t.get("status") != "passed":
        failed_tests.append(t)
        
    # submission status
    if len(failed_tests) == 0:
      status = "passed"
    else: 
      status = "failed"
    rows_submissions.append((submission_id, status))

    # keep only failed tests (and store output/error)
    for t in failed_tests:
        rows_failed.append((
            submission_id,
            t.get("name", ""),
            t.get("status", "failed"),
            (t.get("output") or "").strip()
        ))

# INSERT OR REPLACE INTO tables
# Python3 sqlite3 module: sends all the data to the database in a single block, which is much faster than using INSERT.
cur.executemany(
    "INSERT OR REPLACE INTO submissions(submission_id, status) VALUES (?,?)",
    rows_submissions
)
cur.executemany(
    "INSERT OR REPLACE INTO results_filtered(submission_id, name, status, output) VALUES (?,?,?,?)",
    rows_failed
)

# to save changes directly to the database file
conn.commit()
conn.close()
print(f"Successfully committed to {DB_PATH}. Loaded {len(rows_submissions)} submissions and kept {len(rows_failed)} failed tests.")
