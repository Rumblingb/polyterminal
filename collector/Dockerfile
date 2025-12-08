FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector.py .
COPY paper_trader.py .

CMD ["sh", "-c", "python -u collector.py & python -u paper_trader.py & wait"]
