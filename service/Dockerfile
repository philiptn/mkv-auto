FROM alpine:3.18

WORKDIR /mkv-auto-service

RUN apk update
RUN apk add git docker

COPY .env /mkv-auto-service
COPY service/mkv-auto-builds/mkv-auto.tar /mkv-auto-service
COPY service/entrypoint.sh /mkv-auto-service

RUN mkdir /mkv-auto /mkv-auto/files
RUN chmod +x /mkv-auto-service/entrypoint.sh

ENTRYPOINT ["/mkv-auto-service/entrypoint.sh"]