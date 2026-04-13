FROM python:3.12-slim
WORKDIR /app
COPY agents/shared/ /app/shared/
COPY agents/reviewer/ /app/reviewer/
COPY prompts/ /app/prompts/
RUN pip install --no-cache-dir /app/shared/ /app/reviewer/
CMD ["python", "-m", "reviewer.main"]
