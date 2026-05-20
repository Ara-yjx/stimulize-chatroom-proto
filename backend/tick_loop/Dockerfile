# Small Python image for the heartbeat container. Single-task ECS Fargate
# service running the loop in tick_loop.py. See cdk/lib/tick-heartbeat-stack.ts
# (task 7.6) for runtime config.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tick_loop.py .

# Healthcheck is implicit: the loop exits with code 1 after N failures so
# ECS restarts the task (per LLD, 30s gap on restart is acceptable for beta).
CMD ["python", "-u", "tick_loop.py"]
