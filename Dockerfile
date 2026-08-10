# Базовый образ Python 3.10
FROM python:3.10-slim

# Устанавливаем системные зависимости для Playwright (Chromium)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fonts-liberation \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxkbcommon0 \
    libx11-6 \
    libxcb1 \
    libxext6 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем браузер Chromium для Playwright
RUN playwright install chromium \
    && playwright install-deps chromium || true

# Копируем исходный код
COPY . .

# Healthcheck: Railway проверяет живость контейнера
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Порт для FastAPI/Uvicorn
EXPOSE 8000

# Запуск приложения
CMD ["python", "main.py"]