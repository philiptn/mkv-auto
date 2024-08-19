FROM ubuntu:22.04

COPY prerequisites.sh /mkv-auto/
COPY requirements.txt /mkv-auto/
WORKDIR /mkv-auto
RUN ./prerequisites.sh

COPY scripts /mkv-auto/scripts
COPY utilities /mkv-auto/utilities
COPY defaults.ini /mkv-auto/
COPY subliminal_defaults.toml /mkv-auto/
COPY mkv-auto.py /mkv-auto/
COPY entrypoint.sh /mkv-auto/
COPY service-entrypoint.sh /mkv-auto/

ENTRYPOINT ["/mkv-auto/entrypoint.sh"]