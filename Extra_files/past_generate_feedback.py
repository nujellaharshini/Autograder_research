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
submission_metadata_yml = Path("local_autograder/submissions/assignment_6608314_export/submission_metadata.yml")

MODEL = "gpt-5-mini"
MAX_ASSIGNMENT_CHARS = 1200
MAX_CASES_PER_SUB = 50
MAX_CHARS_PER_OUTPUT = 400

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
    
    "### Example Mappings ###\n" 
    "Autograder error: Test Failed: ['L1', 'L2'] is not false : " 
    "Task 2.2 breadboard must contain L1, L2, R1, R2 within the 4 lines; missing: L1, L2\n" 
    "Feedback: Expect both LED (L) and Resistor (R) breadboard outputs to be printed.\n" 
    "Autograder error: Test Failed: False is not true : " 
    "First-band prompt/choices not found as specified.\n" 
    "Feedback: Prompt text not matching assignment sheet—copy it exactly.\n"
    "### End of Examples ###\n" 
    "Now apply the same reasoning style to the student's errors below.\n" 
    "Give brief, actionable feedback (5 to 15 words per failed test).\n"     
    "Keep it beginner-friendly, factual, and respectful.\n"

)

template = """
Course: Intro Python

Failed tests (names : short messages):
{outputs_block}

Assignment brief (excerpt):
{assignment_excerpt}

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
            submission_id=submission_id
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

if __name__ == "__main__":
    generate_feedback()
