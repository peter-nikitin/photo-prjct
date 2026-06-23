FROM python:3.12

WORKDIR /app/src/backend

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY src/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src/backend/ .

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
