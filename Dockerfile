FROM ubuntu:22.04

COPY . /mkv-auto/

WORKDIR /mkv-auto

RUN ./prerequisites.sh

#RUN apt update
#RUN apt install python3 -y
#RUN apt-get install python3-pip -y
#RUN python3 -m pip install --user virtualenv
#RUN apt install python3.10-venv -y
#RUN python3 -m venv venv
#RUN . venv/bin/activate
#RUN pip3 install --upgrade pip
#RUN pip3 install -r requirements.txt

ENTRYPOINT ["/mkv-auto/entrypoint.sh"]