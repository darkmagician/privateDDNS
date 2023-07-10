from flask import request
from flask import jsonify
from flask import Flask
from datetime import datetime, timedelta
import time
from pyrate_limiter import Duration, RequestRate, Limiter, BucketFullException, Limiter
from dnspod_sdk import DnspodClient
import os


def getENV(key, defaultVal=None):
    if defaultVal:
        return os.getenv(key, default=defaultVal)
    val = os.getenv(key)
    if val:
        return val
    raise Exception(f'env {key} is not configured')


# Environments
HTTP_PORT = getENV('HTTP_PORT', 3053)
CACHE_EXP_IN_SEC = getENV('HTTP_PORT', 3600)
DNSPOD_TOKEN_ID = getENV('DNSPOD_TOKEN_ID')
DNSPOD_TOKEN = getENV('DNSPOD_TOKEN')
DOMAIN = getENV('DOMAIN')
SUB_DOMAIN = getENV('SUB_DOMAIN')

# Global Variables
dc = DnspodClient(DNSPOD_TOKEN_ID, DNSPOD_TOKEN, 'pddns')
hosts_status = {}
dns_cache = None
cache_update_time = 0

client_rate = Limiter(RequestRate(5, Duration.MINUTE))
dns_rate = Limiter(RequestRate(100, Duration.HOUR))

app = Flask(__name__)


@app.route("/hosts/<hostId>", methods=["POST"])
def register(hostId):
    ip = request.remote_addr

    try:
        client_rate.try_acquire(ip)
    except BucketFullException as err:
        return jsonify({'error': f'The client [{ip}] reaches the rate limit.'}), 400
    # print(f'==> Receive from {ip} for {hostId}')
    try:
        record = getDNSValue(hostId)
        if record is None:
            return jsonify({'error': f'unknown host {hostId}'}), 400
        changed = updateDNS(hostId, ip, record)

        return jsonify({'ip': ip, 'changed': changed}), 200
    except BucketFullException as err:
        return jsonify({'error': f'The DNSAPI reaches the rate limit.'}), 400


@app.route("/hosts", methods=["GET"])
def hosts():
    return jsonify(hosts_status), 200


@app.route("/domains", methods=["GET"])
def domains():
    return jsonify(dns_cache), 200


def updateDNS(hostId, ip, record):

    oldIp = record['value']
    lastStatus = hosts_status.get(hostId)
    current = datetime.now()
    hosts_status[hostId] = {'ip': ip, 'updatedTime': current}
    if ip == oldIp:
        return False

    if lastStatus:
        if lastStatus['updatedTime'] + timedelta(minutes=3) > current:
            print(f'{hostId} updates the ip too offen')
    dns_rate.try_acquire('api')
    name = getDNSName(hostId)
    r = dc.post('/Record.Modify', data={'domain': DOMAIN, 'record_id': record['id'], 'record_type': 'A',
                                        'sub_domain': name, 'record_line': '默认', 'value': ip})

    print(r.json())
    print(f'==> Update {name} from {oldIp} to {ip} ')
    refreshDNSCache()
    return True


def refreshDNSCache():
    global dns_cache
    global cache_update_time
    dns_rate.try_acquire('api')
    print("Get Record.List")
    r = dc.post('/Record.List', data={'domain': DOMAIN, 'record_type': 'A'})
    result = r.json()
    cache = {}
    for r in result['records']:
        key = r['name']
        if key.endswith(SUB_DOMAIN):
            cache[key] = r
    dns_cache = cache
    cache_update_time = time.time()


def getDNSName(hostId):
    return f'{hostId}.{SUB_DOMAIN}'


def getDNSValue(host):
    global dns_cache
    global cache_update_time
    print(f'=== {cache_update_time}')
    if dns_cache is None or time.time() > cache_update_time + CACHE_EXP_IN_SEC:
        refreshDNSCache()
    record = dns_cache.get(getDNSName(host))
    return record


def main():
    app.run(host="0.0.0.0", port=HTTP_PORT)


if __name__ == '__main__':
    main()
