FROM ubuntu:22.04

WORKDIR /pre
COPY prerequisites.sh /pre/
COPY requirements.txt /pre/
RUN ./prerequisites.sh

# Create a non-root user and group
RUN groupadd -g 1000 mkv-auto && \
    useradd -m -u 1000 -g mkv-auto mkv-auto

WORKDIR /mkv-auto
COPY scripts /mkv-auto/scripts
COPY utilities /mkv-auto/utilities
COPY defaults.ini /mkv-auto/
COPY subliminal_defaults.toml /mkv-auto/
COPY mkv-auto.py /mkv-auto/
COPY entrypoint.sh /mkv-auto/
COPY service-entrypoint.sh /mkv-auto/
RUN chown -R mkv-auto:mkv-auto /mkv-auto

# Switch to the non-root user
USER mkv-auto

ENTRYPOINT ["/mkv-auto/entrypoint.sh"]