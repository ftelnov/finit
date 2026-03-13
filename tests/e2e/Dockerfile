FROM python:3.12-slim

WORKDIR /tests

# DooD: mount docker socket from host
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["pytest", "-v", "--tb=short", "-x"]
