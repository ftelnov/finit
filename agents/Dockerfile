FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ common/
COPY router/ router/
COPY spec/ spec/
COPY developer/ developer/
COPY reviewer/ reviewer/

# Agent type is set via AGENT_TYPE env var
CMD ["sh", "-c", "python -u ${AGENT_TYPE}/main.py"]
