FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn gevent gevent-websocket

COPY . .

RUN mkdir -p /app/data/drive /app/instance /app/app/static/uploads

ENV FLASK_APP=run.py

EXPOSE 5000

ENTRYPOINT ["/app/entrypoint.sh"]
