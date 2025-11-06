# Dockerfile for the IAM Orchestrator Service
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy requirements and install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code (agents/, orchestrator file, and app.py)
COPY . /app 

# Define the port Cloud Run will use
ENV PORT 8080

# Command to run the Uvicorn server, calling the 'app' object in app.py
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}