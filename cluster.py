import httplib
import os
import shutil
import subprocess

import requests
from retrying import retry

import util

DOCKER_NETWORK = 'couchdb2_cluster_network'
DOCKER_CREATE_NETWORK = 'docker network create --subnet=173.19.0.0/16 {}'.format(DOCKER_NETWORK)
DOCKER_START_NODE = 'docker run --net {cluster_network} --ip="{node_ip}" -v {node_data_dir}:/opt/couchdb/data -d ' \
                    '-v {node_config_dir}:/opt/couchdb/etc --name="{node_name}" docker-couchdb-2:1.0.0'
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
        print('Network setup.')
        subprocess.check_output(DOCKER_CREATE_NETWORK, shell=True)
    except subprocess.CalledProcessError:
        print ('Network exists - ignoring.')

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

    master_node = nodes[0]

    for node in nodes:
        data_dir_path, node_config_path = make_node_config(node.dir, node.ip, node.name)
        start_cmd = DOCKER_START_NODE.format(cluster_network=DOCKER_NETWORK,
                                             node_ip=node.ip,
                                             node_data_dir=data_dir_path,
                                             node_config_dir=node_config_path,
                                             node_name=node.name)
        print(start_cmd)
        subprocess.check_output(start_cmd, shell=True)

        print ("Initializing node")
        initial_configuration(node.ip)
        create_admin_user(node.name, node.ip, admin, "admin", password)
        advanced_configuration(node, admin, password)

    enable_cluster(master_node.ip, admin, password)
    set_cluster_size(master_node.ip, num_nodes, master_node, admin, password)
    add_nodes_to_cluster(master_node.ip, nodes, admin, password)

    response = requests.post(url=COUCHDB_CLUSTER_SETUP['url'].format(user=admin, password=password, ip=master_node.ip),
                             json={"action": "finish_cluster"})
    print('Cluster setup: complete {}'.format(response.text))
    print('Success')


def add_nodes_to_cluster(master_node_ip, nodes, admin, password):
    for node in nodes[1:]:
        url = 'http://{user}:{password}@{master_node_ip}:5986/_nodes/{node_name}@{node_ip}'.format(user=admin,
                                                                                                   password=password,
                                                                                                   master_node_ip=master_node_ip,
                                                                                                   node_name=node.name,
                                                                                                   node_ip=node.ip)
        print ("Adding node {} to cluster {}".format(node.ip, url))
        response = requests.put(url=url, data='{}')
        print (response.text)


def make_node_config(node_dir, node_ip, name):
    local_config_path = os.path.abspath(os.path.join(os.curdir, 'config'))
    data_dir_path = os.path.abspath(os.path.join(os.curdir, node_dir, 'data'))
    node_config_path = os.path.abspath(os.path.join(node_dir, 'local.d'))
    if os.path.exists(data_dir_path) or os.path.exists(node_config_path):
        cmd = 'docker run -v {}:/node_dir --entrypoint="/bin/sh" --rm alpine -c "rm -rf /node_dir/{}" '.format(
            os.path.abspath(os.curdir), node_dir)
        print('Removing existing node config. {}'.format(cmd))
        subprocess.check_output(cmd, shell=True)
    shutil.copytree(local_config_path, node_config_path)
    with open(os.path.join(node_config_path, 'vm.args'), 'r+') as f:
        vm_config = f.read()
        vm_config = vm_config.replace('{{node_name}}', '-name {}@{}'.format(name, node_ip))
        f.seek(0)
        f.write(vm_config)
        f.truncate()

    return data_dir_path, node_config_path


def enable_cluster(master_node_ip, admin, password):
    cluster_url = COUCHDB_CLUSTER_SETUP['url'].format(user=admin, password=password, ip=master_node_ip)
    print ('Cluster setup. {}'.format(cluster_url))
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


def set_cluster_size(ip, size, node, user, password):
    request_or_raise(
        url='http://{user}:{password}@{master_node_ip}:5984/_node/{node_name}@{node_ip}/_config/cluster/n'.format(
            user=user,
            password=password,
            master_node_ip=ip,
            node_name=node.name,
            node_ip=node.ip),
        json='1',
        msg='Cluster setup - set number of copies.')
    request_or_raise(
        url='http://{user}:{password}@{master_node_ip}:5984/_node/{node_name}@{node_ip}/_config/cluster/q'.format(
            user=user,
            password=password,
            master_node_ip=ip,
            node_name=node.name,
            node_ip=node.ip),
        json=size,
        msg='Cluster setup - set number of copies.')

@retry(stop_max_attempt_number=20, wait_fixed=2000)
def initial_configuration(node_ip):
    request_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_users'), msg='Node setup.')
    request_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_replicator'), msg='Node setup.')
    request_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_global_changes'), msg='Node setup.')
    request_or_raise(url=BASE_NODE_URL.format(ip=node_ip, db='_metadata'), msg='Node setup.')


@retry(stop_max_attempt_number=5, wait_fixed=2000)
def create_admin_user(name, node_ip, admin, user, password):
    url = 'http://{}:5984/_node/{}@{}/_config/admins/{}'.format(node_ip, name, node_ip, user)
    request_or_raise(url, json=password, msg='Node setup - creating admin user.')


@retry(stop_max_attempt_number=5, wait_fixed=2000)
def advanced_configuration(node, admin, password):
    # Bind to external/ docker container address
    url = 'http://{}:{}@{}:5984/_node/{}@{}/_config/chttpd/bind_address'.format(admin, password, node.ip,
                                                                                node.name, node.ip)
    request_or_raise(url, json='0.0.0.0', msg='Node setup - enable external access.')

    request_or_raise(
        url='http://{user}:{password}@{master_node_ip}:5984/_node/{node_name}@{node_ip}/_config/couchdb/delayed_commits'.format(
            user=admin,
            password=password,
            master_node_ip=node.ip,
            node_name=node.name,
            node_ip=node.ip),
        json='true',
        msg='Node setup - set delayed commit.')


def request_or_raise(url, json=None, msg=None, method='put'):
    print("{} Request PUT {}".format(msg, url))
    if not hasattr(requests, method):
        raise RuntimeError('Unsupported request method. Must be one of GET, POST, PUT or DELETE.')
    response = getattr(requests, method)(url=url, json=json)
    if response.status_code not in [httplib.CREATED, httplib.OK]:
        raise RuntimeError(
            'Request {} failed with code {}, message {}.'.format(url, response.status_code, response.text))
    return response
