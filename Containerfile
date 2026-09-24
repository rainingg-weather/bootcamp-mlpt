FROM python:3.12-alpine

WORKDIR /app

# Package dasar yang dibutuhkan untuk build pandas/wheels di Alpine
RUN apk add --no-cache build-base gcc musl-dev linux-headers

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]