# Uses Microsoft's official Playwright image — Chrome + all dependencies pre-installed
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app
ARG CHROME_FIRST_BUILD=5aeae337
ENV CHROME_FIRST_BUILD=$CHROME_FIRST_BUILD

# System dependencies (bcrypt, cryptography need build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libssl-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Playwright's Chromium is installed
RUN python -m playwright install chromium

# Application code
COPY . .

EXPOSE 5004
CMD ["python", "app.py"]
