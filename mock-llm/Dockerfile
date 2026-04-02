FROM python:3.12-slim
WORKDIR /app
COPY mock-llm/ /app/mock-llm/
RUN pip install --no-cache-dir /app/mock-llm/
CMD ["python", "-m", "mock_llm.main"]
