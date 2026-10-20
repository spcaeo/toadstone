# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for Playwright and Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright system dependencies for all browsers (requires root)
RUN playwright install-deps

# Create a non-root user to run the application
RUN groupadd -r toadstone && useradd -r -g toadstone -u 1000 toadstone \
    && mkdir -p /home/toadstone && chown -R toadstone:toadstone /home/toadstone

# Copy application code
COPY --chown=toadstone:toadstone . .

# Create directory for user database if it doesn't exist
RUN mkdir -p /app/data && chown -R toadstone:toadstone /app/data

# The bridge wrapper is run as a command by the agent layer, so it has to
# be executable rather than merely present.
RUN chmod +x /app/tools/*.py

# Change ownership of the entire app directory
RUN chown -R toadstone:toadstone /app

# Switch to non-root user
USER toadstone

# Install all Playwright browsers as non-root user (installs to /home/toadstone/.cache/ms-playwright)
RUN playwright install

# Expose Flask port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=main.py
ENV PYTHONUNBUFFERED=1

# Run the application
# The command is handled by docker-compose.yml
CMD ["python", "main.py"]
