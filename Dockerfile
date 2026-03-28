# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies if needed (none for now)

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY Consignabot.py .
COPY event_series.py .
COPY README.md .

# Create data directory
RUN mkdir -p data

# Set environment variable for token (can be overridden at runtime)
ENV DISCORD_TOKEN=""

# Run the bot
CMD ["python", "Consignabot.py"]