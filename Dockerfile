FROM ubuntu:22.04

COPY ./prerequisites.sh /mkv-auto/
COPY ./requirements.txt /mkv-auto/
WORKDIR /mkv-auto
RUN ./prerequisites.sh

COPY . /mkv-auto/
ENTRYPOINT ["/mkv-auto/entrypoint.sh"]