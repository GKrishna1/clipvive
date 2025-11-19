# FastAPI bot service – lightweight production container

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system packages (optional but good)
RUN apt-get update && apt-get install -y curl && apt-get clean

# Copy requirements first (better caching)
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY app/ ./app/

# The bot will run on port 8000
EXPOSE 8000

# Start FastAPI server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

