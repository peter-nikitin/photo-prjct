FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY face_spike/ face_spike/

ENTRYPOINT ["python", "-m", "face_spike"]
