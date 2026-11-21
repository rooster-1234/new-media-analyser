FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Prevent Python from writing .pyc and buffer output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV HOST=0.0.0.0

# Install base dependencies needed for Playwright and Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser and all required Linux system shared libraries
RUN playwright install --with-deps chromium

# Copy application files
COPY . .

# Create volume directories
RUN mkdir -p /app/data /app/output

EXPOSE 8080

CMD ["python", "app.py"]
