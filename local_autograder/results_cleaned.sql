-- table: one row per submission (overall status)
CREATE TABLE IF NOT EXISTS submissions (
  submission_id TEXT PRIMARY KEY,
  status        TEXT  -- "passed" or "failed"
);

-- table: one row per failed test
CREATE TABLE IF NOT EXISTS results_filtered (
  submission_id TEXT,
  name          TEXT,   -- test name
  status        TEXT,   
  output      TEXT, 
  PRIMARY KEY (submission_id, name),
  FOREIGN KEY (submission_id) REFERENCES submissions(submission_id)
);
