FROM python:3.12-slim
WORKDIR /app
COPY agents/shared/ /app/shared/
COPY agents/bootstrapper/ /app/bootstrapper/
COPY prompts/ /app/prompts/
RUN pip install --no-cache-dir /app/shared/ /app/bootstrapper/
CMD ["python", "-m", "bootstrapper.main"]
