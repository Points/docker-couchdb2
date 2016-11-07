import httplib
import os
import shutil
import subprocess

import requests
from retrying import retry

import util

DOCKER_NETWORK = 'couchdb2_cluster_network'
DOCKER_CREATE_NETWORK = 'docker network create --subnet=173.19.0.0/16 {}'.format(DOCKER_NETWORK)
DOCKER_START_NODE = 'docker run --net {cluster_network} --ip="{node_ip}" -v {node_dir}:/opt/couchdb/data -d ' \
                    '-v {node_etc_dir}:/opt/couchdb/etc --name="{node_name}" dev-docker.points.com:80/couchdb2:2.0.0'
DOCKER_FIND_NODE = 'docker ps --filter "name={node_name}" -qa'
BASE_NODE_URL = 'http://{ip}:5984/{db}'
NODE_URL = 'http://{ip}:5984/{slug}'
SECURED_NODE_URL = 'http://{user}:{password}@{ip}:5984/{slug}'
COUCHDB_CLUSTER_SETUP = {
    'url': 'http://{user}:{password}@{ip}:5984/_cluster_setup',
    'payload': '',
    'finish_payload': '{"action":"finish_cluster"}'
}


def start(num_nodes, admin, password):
    try:
        subprocess.check_output(DOCKER_CREATE_NETWORK, shell=True)
    except subprocess.CalledProcessError:
        print ("network exists - ignoring.")

    nodes = []
    for node_num in range(2, int(num_nodes) + 2):
        node_ip = '173.19.0.{}'.format(node_num)
        node_dir = 'node{}'.format(node_num)
        container_name = 'couchdb' + node_dir
        nodes.append(util.node(node_dir, node_ip, container_name))

        try:
            container_id = subprocess.check_output(DOCKER_FIND_NODE.format(node_name=container_name), shell=True)
            if container_id:
                print ("removing container {} {}".format(container_name, container_id))
                subprocess.check_output('docker rm -f {}'.format(container_id), shell=True)
        except:
            pass  # Ignore exceptions for now.
    import ipdb
    ipdb.set_trace()
    for node in nodes:
        node_dir_path, node_config_path = make_node_config(node.dir, node.ip, node.name)
        start_cmd = DOCKER_START_NODE.format(cluster_network=DOCKER_NETWORK,
                                             node_ip=node.ip,
                                             node_dir=node_dir_path,
                                             node_etc_dir=node_config_path,
                                             node_name=node.name)
        print start_cmd
        subprocess.check_output(start_cmd, shell=True)

        print ("Initializing node")
        initial_configuration(node.ip)
        create_admin_user(node.name, node.ip, admin, "admin", password)
        advanced_configuration(node.name, node.ip, admin, password, "admin")

    master_node_ip = nodes[0].ip
    # import ipdb
    # ipdb.set_trace()
    print ("Enabling cluster")
    enable_cluster(master_node_ip, admin, password)
    add_nodes_to_cluster(master_node_ip, nodes, admin, password)

    response = requests.post(
        url=COUCHDB_CLUSTER_SETUP['url'].format(user=admin, password=password, ip=master_node_ip),
        json={"action": "finish_cluster"})


def add_nodes_to_cluster(master_node_ip, node_ips, admin, password):
    for node in node_ips[1:]:
        url = 'http://{user}:{password}@{master_node_ip}:5984/_cluster_setup'.format(user=admin,
                                                                                     password=password,
                                                                                     master_node_ip=master_node_ip)
        print ("Adding node {} to cluster {}".format(node.ip, url))
        response = requests.post(url=url, json={"action": "add_node",
                                                "host": node.ip,
                                                "username": admin,
                                                "password": password
                                                })
        print (response.text)


def make_node_config(node_dir, node_ip, name):
    config_path = os.path.abspath(os.path.join(os.curdir, 'config'))
    node_dir_path = os.path.abspath(os.path.join(os.curdir, node_dir))
    if os.path.exists(node_dir_path):
        cmd = 'docker run -v {}:/node_dir --entrypoint="/bin/sh" --rm alpine -c "rm -rf /node_dir/{}" '.format(
            os.path.abspath(os.curdir), node_dir)
        subprocess.check_output(cmd, shell=True)
    node_config_path = os.path.abspath(os.path.join(node_dir_path, 'config'))
    shutil.copytree(config_path, node_config_path)
    with open(os.path.join(node_config_path, 'vm.args'), 'r+') as f:
        vm_config = f.read()
        vm_config = vm_config.replace('{{node_name}}', '-name {}@{}'.format(name, node_ip))
        f.seek(0)
        f.write(vm_config)
        f.truncate()

    return node_dir_path, node_config_path


def enable_cluster(master_node_ip, admin, password):
    cluster_url = COUCHDB_CLUSTER_SETUP['url'].format(user=admin, password=password, ip=master_node_ip)
    print (cluster_url)
    response = requests.post(
        url=cluster_url,
        json={"action": "enable_cluster",
              "bind_address": "0.0.0.0",
              "port": 5984,
              "username": admin,
              "password": password
              }
    )
    if response.status_code != httplib.CREATED and 'Cluster is already enabled' not in response.text:
        raise RuntimeError('Unable to setup cluster. {}'.format(response.text))


@retry(stop_max_attempt_number=20, wait_fixed=2000)
def initial_configuration(node_ip):
    put_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_users'))
    put_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_replicator'))
    put_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_global_changes'))
    put_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_metadata'))


@retry(stop_max_attempt_number=5, wait_fixed=2000)
def create_admin_user(name, node_ip, admin, user, password):
    # Setup admin user
    url = 'http://{}:5984/_node/{}@{}/_config/admins/{}'.format(node_ip, name, node_ip, user)
    put_or_raise(url, json=password)


@retry(stop_max_attempt_number=5, wait_fixed=2000)
def advanced_configuration(name, node_ip, admin, password, user):
    # Bind to external/ docker container address
    url = 'http://{}:{}@{}:5984/_node/{}@{}/_config/chttpd/bind_address'.format(admin, password, node_ip,
                                                                                name, node_ip)
    put_or_raise(url, json='0.0.0.0')


def put_or_raise(url, json=None):
    print("Request PUT {}".format(url))
    response = requests.put(url=url, json=json)
    if response.status_code not in [httplib.CREATED, httplib.OK]:
        raise RuntimeError('Request {} failed with code {}.'.format(url, response.status_code))
    return response
