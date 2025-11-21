# Dockerfile for the IAM Orchestrator Service
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# 1. Copy requirements and install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copy the token specifically first (Optional, but fails fast if missing)
COPY orchestrator_key.json .

# 3. Copy the rest of the application code
COPY . .

# Define the port Cloud Run will use
ENV PORT 8080

# Command to run the Uvicorn server
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
