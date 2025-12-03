# Used the official Gradescope autograder base image
FROM gradescope/autograder-base 
RUN python3 -m pip install --upgrade pip

# Copy autograder source code
WORKDIR /autograder/source
COPY autograder/ /autograder/source/

# Python dependencies
RUN pip3 install -r /autograder/source/requirements.txt

# Add run_autograder script
COPY run_autograder /autograder/run_autograder
RUN chmod +x /autograder/run_autograder
