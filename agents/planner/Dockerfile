FROM python:3.12-slim
WORKDIR /app
COPY agents/shared/ /app/shared/
COPY agents/planner/ /app/planner/
RUN pip install --no-cache-dir /app/shared/ /app/planner/
CMD ["python", "-m", "planner.main"]
