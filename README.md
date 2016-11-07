# docker-couchdb2
Setup a local couchdb2 cluster with docker

* Setup a virtualenv
```
$ mkvirtualenv docker-couchdb
$ pip install -r requirements.txt
```
* Start a cluster with 3 nodes
```    
$ python main.py -u admin -p password -n 3
```