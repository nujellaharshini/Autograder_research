#!/usr/bin/env python3
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
import os, json, sqlite3
from typing import List, Tuple, Dict, Optional
import yaml 

db_path = Path("local_autograder/tpch.sqlite")
assignment = Path("assignment.txt")
feedback_json = Path("feedback.json")
submission_metadata_yml = Path("local_autograder/submissions/assignment_7023745_export/submission_metadata.yml")
submissions_root = Path("local_autograder/submissions/assignment_7023745_export/")
error_bank_path = Path("error_bank.json")
student_total_path = Path("student_feedback_total.json")
MODEL = "gpt-5-mini"
MAX_ASSIGNMENT_CHARS = 1200
MAX_CASES_PER_SUB = 50
MAX_CHARS_PER_OUTPUT = 400
MAX_CODE_CHARS = 1600


def read_excerpt(p: Path, limit: int) -> str:
    if not p.exists():
        return ""
    return p.read_text(errors="ignore")[:limit].strip()

# Condences the ouput message so it doesn't hit the limit in the prompt
def condense(msg: str, limit: int) -> str:
    if not msg:
        return ""
    one = " ".join(line.strip() for line in msg.splitlines() if line.strip())
    return one.replace("\\\\", "\\")[:limit]

def fetch_grouped_outputs(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    # Fetch all outputs from results_filtered, grouped by submission_id
    q = """
    SELECT submission_id, COALESCE(output,'')
    FROM results_filtered
    ORDER BY submission_id;
    """
    grouped: Dict[str, List[str]] = {}
    for sid, output in conn.execute(q):
        grouped.setdefault(sid, []).append(output or "")
    return grouped

def find_submission_dir(root: Path, submission_id: str) -> Optional[Path]:
    """
    Try to locate the directory for a given submission_id.
    We try both 'submission_123' and '123' just in case.
    """
    candidates = [
        root / submission_id,                                   # 'submission_342630991'
        root / submission_id.replace("submission_", ""),        
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p
    # As a fallback, scan dirs that start with submission_id 
    for p in root.glob(f"{submission_id}*"):
        if p.is_dir():
            return p
    return None

def read_student_code_excerpt(sub_dir: Optional[Path], limit: int = MAX_CODE_CHARS) -> str:
    if not sub_dir or not sub_dir.exists():
        return ""
    pieces, total = [], 0
    py_files = sorted(sub_dir.glob("*.py"))
    for py in py_files:
        header = f"# FILE: {py.name}\n"
        try:
            code = py.read_text(errors="ignore")
        except Exception:
            code = ""
        chunk = header + code
        if total + len(chunk) > limit:
            chunk = chunk[: max(0, limit - total)]
        pieces.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return ("\n".join(pieces)).strip()

def load_name_map_from_yaml(yml_path: Path) -> Dict[str, Dict[str, Optional[str]]]:
    # Load submission metadata from the YAML file exported from Canvas
    data = yaml.safe_load(yml_path.read_text(errors="ignore")) or {}
    out: Dict[str, Dict[str, Optional[str]]] = {}

    for sub_key, payload in (data.items() if isinstance(data, dict) else []):
        # payload[:submitters] is usually a list; take the first submitter
        submitters = []
        if isinstance(payload, dict):
            submitters = payload.get(":submitters") or payload.get("submitters") or []
        full_name = sid = email = None
        if submitters and isinstance(submitters, list):
            s0 = submitters[0]
            # keys sometimes have a leading ":" depending on how it was serialized so made it flexible
            full_name = (s0.get(":name") or s0.get("name") or "").strip() or None
            sid = (s0.get(":sid") or s0.get("sid") or "")
            email = (s0.get(":email") or s0.get("email") or "")
        out[sub_key] = {
            "full_name": full_name,
            "sid": str(sid) if sid is not None else None,
            "email": email or None
        }
    return out

System_prompt = (
    "You are a teaching assistant for an introductory Electrical Engineering programming course (Python).\n" 
    "Your job is to interpret autograder error messages and give short, corrective feedback.\n" 
    "Provide constructive comments that help students understand what to fix without revealing full solutions.\n" 
    "Your meta-goal is to help students *understand why* their autograder tests failed and what to try next.\n"

    "\n" 
    "### Example Mappings ###\n" 
    
    "Autograder error: Test Failed: Your program raised an exception: leading zeros in decimal integer literals are not permitted; use an 0o prefix for octal integers (<string>, line 2) " 
    "Task 2.2 breadboard must contain L1, L2, R1, R2 within the 4 lines; missing: L1, L2\n" 
    "Feedback: Wrap the heading and list lines in print() strings; don't paste plain output. \n" 

    "\n" 

    "Autograder error: Expected updated list to contain [Value: 220 ohms] (flexible spacing/case)." 
    "Expected updated list to contain [Value: 330 ohms] (flexible spacing/case)."
    "Feedback: Format printed values as plain text, not Python lists; syntax is incorrect. \n" 

    "### End of Examples ###\n" 

    "\n" 

    "Now apply the same reasoning style to the student's errors below.\n" 

    "Give brief, actionable feedback (5 to 15 words per failed test).\n" 
    "Output must be a single valid JSON object with the keys shown.\n"
    "Do not write any text before or after the JSON.\n"
    
    "Keep it beginner-friendly, factual, and respectful.\n" 
)

template = """
Course: Intro Python

Failed tests (names : short messages):
{outputs_block}

Assignment brief (excerpt):
{assignment_excerpt}

Student submission (excerpt):
{code_excerpt}

Return JSON exactly in this format:
{{
  "submission_id": "{submission_id}",
  "feedback": [
    {{"message":"<overall 5 to 25 word comment>"}}
  ]
}}
"""

def generate_feedback():
    load_dotenv()
    client = OpenAI()  

    name_map = load_name_map_from_yaml(submission_metadata_yml)
    assignment_excerpt = read_excerpt(assignment, MAX_ASSIGNMENT_CHARS)

    conn = sqlite3.connect(db_path)
    outputs_by_sid = fetch_grouped_outputs(conn)
    conn.close()

    results_out = []
    # Go through the failed tests grouped by submission_id
    for submission_id, outputs in outputs_by_sid.items():
        # Takes only the first MAX_CASES_PER_SUB outputs, each condensed to MAX_CHARS_PER_OUTPUT
        condensed = [condense(o, MAX_CHARS_PER_OUTPUT) for o in outputs[:MAX_CASES_PER_SUB]]
        # Join them into a single block for the prompt
        outputs_block = "\n- ".join([""] + condensed) if condensed else "(none)"

        user_prompt = template.format(
            outputs_block=outputs_block,
            assignment_excerpt=assignment_excerpt or "(none)",
            submission_id=submission_id,
            code_excerpt=read_student_code_excerpt(find_submission_dir(submissions_root, submission_id), MAX_CODE_CHARS) or "(none)"
        )

        resp = client.responses.create(
            model=MODEL,
            input=[
                {"role": "system", "content": System_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = resp.output_text

        try:
            parsed = json.loads(text)
            fb_list = parsed.get("feedback", [])
            msg = (fb_list[0].get("message", "").strip() if fb_list else "")
            full_name = (name_map.get(submission_id, {}) or {}).get("full_name") or ""

            if msg:
                results_out.append({
                    "submission_id": submission_id,
                    "full_name": full_name,
                    "feedback": msg
                })
                print(f"{submission_id}: {full_name} and its message: {msg}")
            else:
                # Error message
                print(f"No feedback for {submission_id}")
        except Exception as e:
            # JSON parse error
            print(f"JSON parse failed for {submission_id}: {e}\n{text[:400]}")

    existing = []
    if feedback_json.exists():
        try:
            existing = json.loads(feedback_json.read_text())
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    # Merge existing feedback with new feedback, prioritizing new feedback and creating a dictionary keyed by submission_id
    merged = {}
    for row in existing:
        key = row["submission_id"]
        merged[key] = row
        
    for row in results_out:
        merged[row["submission_id"]] = row

    feedback_json.write_text(json.dumps(list(merged.values()), indent=2))
    print(f"\nWrote to feedback_prompt_3.json with {len(merged)} records.")
