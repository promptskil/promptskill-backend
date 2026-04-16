FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Default: web service. Worker and beat override via
# Railway Custom Start Command in dashboard.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
