FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --no-cache-dir pytest pytest-asyncio httpx psycopg2-binary sseclient-py
COPY . /app/
CMD ["python", "-m", "pytest", "-v", "--tb=short", "-x"]
