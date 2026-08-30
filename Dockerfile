FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY configs/ ./configs/

# Entrypoint is a placeholder until src/main.py exists — see
# docs/00_project_plan.md for the Day-by-day build sequence.
CMD ["sleep", "infinity"]
