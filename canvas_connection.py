import os, sys, time, json, requests
from pathlib import Path
from typing import List, Optional, Dict
from dotenv import load_dotenv

load_dotenv()
BASE_URL = os.getenv("CANVAS_BASE_URL", "https://catcourses.ucmerced.edu").rstrip("/")
COURSE_ID = os.getenv("COURSE_ID")
ASSIGNMENT_ID = os.getenv("ASSIGNMENT_ID")
CANVAS_TOKEN = os.getenv("CANVAS_API_KEY")
FEEDBACK_JSON = Path("feedback.json")
RATE_LIMIT_SLEEP = float(os.getenv("RATE_LIMIT_SLEEP", "0.35"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "" # default to dry run mode if not set
ASSIGNMENT_NAME = os.getenv("ASSIGNMENT_NAME")
    
# Initializing the session for making requests
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {CANVAS_TOKEN}"})

# Handles API calls to Canvas and checks for errors
def canvas_request(method: str, path: str, params: Optional[Dict]=None, data: Optional[Dict]=None):
    url = f"{BASE_URL}{path}"
    r = S.request(method, url, params=params, data=data, timeout=30)
        
    if r.status_code >= 400:
        raise requests.HTTPError(f"API Error {r.status_code} for {path}: {r.text[:100]}", response=r)
    return r

def lookup_user(course_id: str, full_name: str) -> Optional[int]:
    path = f"/api/v1/courses/{course_id}/users"
    params = {"search_term": full_name, "per_page": 10}
    
    r = canvas_request("GET", path, params=params)
    users = r.json() or []

    # Filter for an exact match on the student's name.
    exacts = []
    for i in users:
        if i.get("name") == full_name:
            exacts.append(i)
    
    if len(exacts) == 1:
        print(f"Found unique match for '{full_name}'. User ID: {exacts[0].get('id')}", file=sys.stderr)
        return exacts[0].get("id")
    return None

def post_submission_comment(course_id: str, assignment_id: str, user_id: int, comment_text: str, dry_run: bool=True):    
    path = f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"
    
    # dry run mode: do not actually post the comment
    if dry_run:
        return {"dry_run": True, "comment_length": len(comment_text)}
    
    data = {"comment[text_comment]": comment_text}
    r = canvas_request("PUT", path, data=data)
        
    return r.json()

def send_canvas_message(recipient_id: int, assignment_name: str):
    path = "/api/v1/conversations"
    subject = f"New Comment on {assignment_name}"
    body = f"""\
    Dear student,
    If you are receiving this message, it is because there is a new comment for your assignment:
    **{assignment_name}**

    Please log in to Canvas to review the feedback. 
"""
    data = {
        "recipients[]": [recipient_id],
        "subject": subject,
        "body": body,
        "context_code": f"course_{COURSE_ID}"
    }
    
    try:
        r = canvas_request("POST", path, data=data)
        print(f"Notification sent to user {recipient_id}")
        return r.json()
    except Exception as e:
        print(f"Failed to send message to {recipient_id}: {e}", file=sys.stderr)
        return None

def main():
    rows: List[dict] = json.loads(FEEDBACK_JSON.read_text(encoding="utf-8"))
    sent = 0
    
    for i, row in enumerate(rows):
        student_name = (row.get("full_name") or "").strip()
        feedback = (row.get("feedback") or "").strip()
        
        # Skip rows that don't have enough data
        if not student_name or not feedback:
            continue
        
        try:
            # Step 1: Find the student's Canvas ID using their Full Name
            user_id = lookup_user(COURSE_ID, student_name)
            if user_id is None:
                continue
                
            # Step 2: Post the comment
            post_submission_comment(COURSE_ID, ASSIGNMENT_ID, user_id, feedback, dry_run=DRY_RUN)
            sent += 1
            time.sleep(RATE_LIMIT_SLEEP) 
            
            if not DRY_RUN:
                send_canvas_message(user_id, ASSIGNMENT_NAME)
                # Pause to respect Canvas rate limits
                time.sleep(RATE_LIMIT_SLEEP) 

        except Exception as e:
            # Any network or API errors
            print(f"Error for {student_name}")

    print(f"\n--- Process Complete ---")
    print(f"Total Sent: {sent}")
    print(f"DRY RUN was: {DRY_RUN}")
    
if __name__ == "__main__":
    main()