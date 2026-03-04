FROM python:3.11-slim

# System deps for OpenCV + DeepFace
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download ArcFace model weights at build time
# So it doesn't download on first request
RUN python -c "from deepface.modules import modeling; modeling.build_model('ArcFace')" || true

COPY . .

RUN mkdir -p /app/storage/photos

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
