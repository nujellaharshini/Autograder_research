#!/usr/bin/env python3
import json, os, sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results"))

    
all_results = {} 
for student in sorted(os.listdir(root)):
    res = os.path.join(root, student, "results.json")
    if os.path.isfile(res):
        with open(res) as f:
            all_results[student] = json.load(f)
    else:
        all_results[student] = {"error": "no results.json"}

with open("results_all.json", "w") as out: # write all results to results_all.json
    json.dump(all_results, out, indent=2)

print("Wrote results_all.json") # notify of completion in terminal
