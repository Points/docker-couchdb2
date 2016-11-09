FROM klaemo/couchdb:2.0.0

COPY ./docker-entrypoint.sh /

RUN apt-get update

RUN apt-get -y install python-pip

RUN pip install couchdb

RUN chmod +x /docker-entrypoint.sh
