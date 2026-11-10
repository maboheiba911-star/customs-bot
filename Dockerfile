FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# app.db (users, fx rates) and updated reference.db live in DATA_DIR; mount a volume there to keep them
ENV DATA_DIR=/data
VOLUME ["/data"]

CMD ["python", "bot.py"]
