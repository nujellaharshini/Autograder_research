#!/usr/bin/env python3
from openai import OpenAI
from dotenv import load_dotenv
import re
from pathlib import Path
import os, json, sqlite3
from typing import List, Dict, Optional, Tuple
import yaml 
import tempfile, time, json
from collections import Counter

db_path = Path("local_autograder/tpch.sqlite")
assignment = Path("assignment.txt")
feedback_json = Path("feedback.json")
submission_metadata_yml = Path("local_autograder/submissions/assignment_7023745_export/submission_metadata.yml")
submissions_root = Path("local_autograder/submissions/assignment_7023745_export/")
error_bank_path = Path("error_bank.json")
student_all_errors_path = Path("student_feedback_total.json")
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

def load_error_bank(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())

"""
Returns a deduplicated list of error codes matched by regex in error_bank.json.
Falls back to ['UNCLASSIFIED'] if nothing matches.
"""
def classify_errors_with_bank(raw_messages: List[str], bank: Dict[str, dict]) -> List[str]:
    codes = []
    for i in raw_messages:
        matched = False
        for code, entry in bank.items():
            if code == "UNCLASSIFIED":
                continue
            # Try each regex pattern in this bank entry
            for pat in entry.get("regex", []):
                # re.I - ignore case sensitivity
                # re.S - dot matches newlines
                try: 
                    if re.search(pat, i, re.I | re.S):
                        codes.append(code)
                        matched = True
                        break
                except re.error:
                    continue
            if matched:
                break
        if not matched:
            codes.append("UNCLASSIFIED")

    # remove duplicates 
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out or ["UNCLASSIFIED"]

def load_student_errors(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try: 
        data = json.loads(path.read_text())
        if isinstance(data, dict): 
            return data
        else: 
            return {}
    except json.JSONDecodeError:
        print(f"Warning: starting with empty dict.")
        return {}
        
def save_student_errors(path: Path, data: Dict[str, dict]) -> None:
    # path.write_text(json.dumps(data, indent=1))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tmp:
        tmp.write(json.dumps(data, indent=2))
        temp_name = tmp.name
    Path(temp_name).replace(path)


def record_student_submission(
    totals: Dict[str, dict],
    full_name: str,
    submission_id: str,
    error_codes: List[str],
    feedback_text: Optional[str] = None
) -> Dict[str, dict]:
    student = totals.setdefault(full_name, {
        "full_name": full_name,
        "feedback_count": 0,
        "history": []    # [{ts, assignment_id, submission_id, error_codes, feedback_key}]
    })

    student["feedback_count"] += 1

    # append to history
    student["history"].append({
        "submission_id": submission_id,
        "error_codes": error_codes,
        "feedback": feedback_text or ""
    })

    totals[full_name] = student
    return totals
 
def make_student_pattern_block_for_submission(bank: Dict[str, dict], error_codes: List[str]) -> str:
    if not error_codes:
        return "none"
    by_category: Dict[str, List[str]] = {}
    for i in error_codes:
        category = bank.get(i, {}).get("category", "Unclassified")
        by_category.setdefault(category, []).append(i)
    lines = []
    for category, codes in by_category.items():
        formatted_codes = ", ".join(sorted(codes))
        line = f"{category}: {formatted_codes}"
        lines.append(line)
        
    return "\n".join(lines)

HUMANIZE = {
    "TYPE_NONE": "mixing None with numbers/operations",
    "FLOAT_ON_NONE": "calling float() on None",
    "OUTPUT_MISMATCH": "output formatting mismatch",
    "MATH_OPS_INCORRECT": "incorrect math operations",
    "LOOP_NEVER_UPDATES": "loop condition never updates (possible infinite loop)",
    "LOOP_BAD_RANGE": "range() bounds/off-by-one",
    "FUNC_ARGS_ORDER": "wrong function arguments or order",
    "FUNC_MISSING_RETURN": "missing return (returning None)",
    "TYPE_MIX_STR_INT": "mixing str and int without casting",
    "FILE_PATH_ERROR": "bad file path or missing file"
}

def summarize_history(student: Dict[str, dict],
                      now_codes: List[str],
                      top_k: int = 3,
                      window: int = 5) -> Tuple[str, List[str]]:
    if not student or not student.get("history"):
        return "(no prior feedback)", []

    hist = student["history"]

    # Lifetime counts
    life = Counter()
    for h in hist:
        life.update(h.get("error_codes", []))

    # Recent counts (last `window` submissions)
    recent = Counter()
    for h in hist[-window:]:
        recent.update(set(h.get("error_codes", [])))  # count once per submission

    # Determine frequent historical codes
    frequent = {c for c, n in life.items() if n >= 2} | {c for c, n in recent.items() if n >= 2}
    # Overlap with current codes (what we’ll ask the LLM to personalize on)
    overlap = sorted(list(set(now_codes) & frequent))

    # Format top-k lifetime for display
    top = [f"{c}({life[c]})" for c, _ in life.most_common(top_k)]
    lines = [
        f"Total prior feedback items: {len(hist)}",
        ("Most frequent codes: " + ", ".join(top)) if top else "Most frequent codes: (none)",
        ("Current codes: " + ", ".join(now_codes)) if now_codes else "Current codes: (none)"
    ]
    if overlap:
        # Also include humanized hint to help the LLM phrase it naturally
        human = [HUMANIZE.get(c, c) for c in overlap]
        lines.append("Frequent codes also present now: " + ", ".join(overlap))
        lines.append("Humanized: " + "; ".join(human))

    return "\n".join(lines), overlap

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
    
    "\n"
    "### Personalization Rule ###\n"
    "If the student's history shows a frequent error code that also appears in the current submission, "
    "add one final sentence like:\n"
    "\"We’ve noticed this has recurred (e.g., mixing None with numbers) — please review that concept before resubmitting.\"\n"
    "If there is no overlap, do not mention history.\n"
    
    "Keep it beginner-friendly, factual, and respectful.\n" 
    "Do not include any text before or after the JSON.\n"
)

template = """
Course: Intro Python

Failed tests (names : short messages):
{outputs_block}

Assignment brief (excerpt):
{assignment_excerpt}

Student submission (excerpt):
{code_excerpt}

Detected error categories (this submission):
{student_pattern_block}

Student history (compact):
{student_history_block}

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

    error_bank = load_error_bank(error_bank_path)
    student_totals = load_student_errors(student_all_errors_path)
    
    conn = sqlite3.connect(db_path)
    outputs_by_sid = fetch_grouped_outputs(conn)
    conn.close()

    results_out = []
    # Go through the failed tests grouped by submission_id
    for submission_id, outputs in outputs_by_sid.items():
        # Takes only the first MAX_CASES_PER_SUB outputs, each condensed to MAX_CHARS_PER_OUTPUT
        condensed = [condense(o, MAX_CHARS_PER_OUTPUT) for o in outputs[:MAX_CASES_PER_SUB]]
        raw_errors = condensed[:]
        
        error_codes = classify_errors_with_bank(raw_errors, error_bank)
        meta = (name_map.get(submission_id, {}) or {})
        full_name = (meta.get("full_name") or "").strip()
        
        # load/update per-student totals BEFORE calling the LLM
        # student_profile = student_totals.get(full_name, {})
        student_profile = student_totals.get(full_name, {})

        history_block, overlap_codes = summarize_history(
            student=student_profile,
            now_codes=error_codes,
            top_k=3, #3 most frequent error codes
            window=5 #last 5 submissions
        )
        pattern_block = make_student_pattern_block_for_submission(error_bank, error_codes)

        # Join them into a single block for the prompt
        outputs_block = "\n- ".join([""] + condensed) if condensed else "(none)"
        
        user_prompt = template.format(
            outputs_block=outputs_block,
            assignment_excerpt=assignment_excerpt or "(none)",
            submission_id=submission_id,
            code_excerpt=read_student_code_excerpt(find_submission_dir(submissions_root, submission_id), MAX_CODE_CHARS) or "(none)",
            student_pattern_block=pattern_block,
            student_history_block=history_block
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

            student_totals = record_student_submission(
                student_totals,
                full_name=full_name,
                submission_id=submission_id,
                error_codes=error_codes,
                feedback_text=msg if msg else None
            )

            if msg:
                results_out.append({
                    "submission_id": submission_id,
                    "full_name": full_name,
                    "feedback": msg,
                })
                print(f"{submission_id}: {full_name} and its message: {msg}")
            else:
                print(f"No feedback for {submission_id}")

        except Exception as e:
            print(f"JSON parse failed for {submission_id}: {e}\n{text[:400]}")
            
            
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

    save_student_errors(student_all_errors_path, student_totals)
    
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
    print(f"\nWrote to feedback.json with {len(merged)} records.")
    
if __name__ == "__main__":
    generate_feedback()

