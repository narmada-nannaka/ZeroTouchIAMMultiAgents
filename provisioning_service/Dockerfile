# provisioning_service/Dockerfile (CRITICAL FIX: Explicit Port)
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy both the app.py and provisioning_agent.py
COPY app.py .
COPY provisioning_agent.py .

# Define the port Cloud Run will use
ENV PORT=8080

# Run uvicorn directly with the app object, binding to all interfaces
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}