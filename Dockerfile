FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Cache-bust: force Railway to rebuild all layers fresh.
# Remove this line once builds are confirmed working.
RUN echo "cache-bust-2026-04-16"
# Default: web service. Worker and beat override via
# Railway Custom Start Command in dashboard.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
