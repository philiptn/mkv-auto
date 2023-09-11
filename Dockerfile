FROM ubuntu:22.04

WORKDIR /mkv-auto

COPY . .

RUN ./prerequisites.sh

CMD ["source", "venv/bin/activate"]
CMD ["python3", "mkv-auto.py"]