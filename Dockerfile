FROM python:3.10-slim

# Install build dependencies
RUN apt-get update && \
    apt-get install -y build-essential wget curl git libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Start the app
CMD ["python", "anti_matrix_bot_enhanced.py"]
