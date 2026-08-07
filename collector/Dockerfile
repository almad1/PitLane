FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# -u = unbuffered stdout so Docker logs appear immediately
CMD ["python", "-u", "main.py"]
