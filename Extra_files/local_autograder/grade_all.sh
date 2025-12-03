#!/usr/bin/env bash
set -euo pipefail

IMAGE="my-autograder"
ROOT="submissions/assignment_7023745_export" # path to submissions directory 
OUT="results"

mkdir -p "$OUT" # ensure output directory exists if not creates results

for student_dir in "$ROOT"/*; do
  [ -d "$student_dir" ] || continue # if not a directory, skip
  student="$(basename "$student_dir")" # get student name
  echo "==> Grading $student" # print student name in terminal
  mkdir -p "$OUT/$student" # create output directory for student if not exists
  # Run autograder in Docker
  # type of mount : where the file comes from : where to put it : read-only or read-write
  docker run --rm \
    --platform=linux/amd64 \
    --mount type=bind,source="$PWD/$student_dir",target=/autograder/submission,readonly \
    --mount type=bind,source="$PWD/$OUT/$student",target=/autograder/results \
    "$IMAGE" /autograder/run_autograder || echo "   (failed for $student)"
done