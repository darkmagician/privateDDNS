from flask import request
from flask import jsonify
from flask import Flask
from datetime import datetime, timedelta
import time
from pyrate_limiter import Duration, RequestRate, Limiter, BucketFullException, Limiter
from dnspod_sdk import DnspodClient
import os
from threading import RLock
import socket
import threading
import traceback


def getENV(key, defaultVal=None):
    if defaultVal:
        return os.getenv(key, default=defaultVal)
    val = os.getenv(key)
    if val:
        return val
    raise Exception(f'env {key} is not configured')


def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        # doesn't even have to be reachable
        s.connect(('114.114.114.114', 53))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


# Environments
HTTP_PORT = getENV('HTTP_PORT', 3053)
CACHE_EXP_IN_SEC = getENV('HTTP_PORT', 3600)
DNSPOD_TOKEN_ID = getENV('DNSPOD_TOKEN_ID')
DNSPOD_TOKEN = getENV('DNSPOD_TOKEN')
DOMAIN = getENV('DOMAIN')
SUB_DOMAIN = getENV('SUB_DOMAIN')
MY_DOMAIN = getENV('MY_DOMAIN', '')

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
    hostId = hostId.lower()
    try:
        client_rate.try_acquire(ip)
    except BucketFullException as err:
        return jsonify({'error': f'The client [{ip}] reaches the rate limit.'}), 400
    # print(f'==> Receive from {ip} for {hostId}')
    try:
        record = getDNSValue(hostId)
        # if record is None:
        #     return jsonify({'error': f'unknown host {hostId}'}), 400
        changed = updateDNS(hostId, ip, record)
        cleanDNS()
        return jsonify({'ip': ip, 'changed': changed}), 200
    except BucketFullException as err:
        return jsonify({'error': f'The DNSAPI reaches the rate limit.'}), 400


@app.route("/status", methods=["GET"])
def status():
    return jsonify(hosts_status), 200


@app.route("/hosts", methods=["GET"])
def hosts():
    records = [value['ip'] + ' ' + getDNSDomain(key) for key, value in hosts_status.items()]
    return '\n'.join(records), 200, {'Content-Type': 'text/plain;charset=utf-8'}


@app.route("/domains", methods=["GET"])
def domains():
    return jsonify(dns_cache), 200


def updateDNS(hostId, ip, record):

    lastStatus = hosts_status.get(hostId)
    current = datetime.now()
    hosts_status[hostId] = {'ip': ip, 'updatedTime': current}

    oldIp = None
    if record:
        oldIp = record['value']
        if ip == oldIp:
            return False

    if lastStatus:
        if lastStatus['updatedTime'] + timedelta(minutes=3) > current:
            print(f'{hostId} updates the ip too offen')

    dns_rate.try_acquire('api')
    name = getDNSName(hostId)
    if record:
        r = dc.post('/Record.Modify', data={'domain': DOMAIN, 'record_id': record['id'], 'record_type': 'A',
                                            'sub_domain': name, 'record_line': '默认', 'value': ip})
    else:
        r = dc.post('/Record.Create', data={'domain': DOMAIN, 'record_type': 'A',
                                            'sub_domain': name, 'record_line': '默认', 'value': ip})

    print(r.json())
    print(f'==> Update {name} from {oldIp} to {ip} ')
    refreshDNSCache()
    return True


def doCleanDNS(current):
    print("start to clean DNS")
    activeRecords = []
    for key, val in hosts_status.items():
        if val['updatedTime'] + timedelta(days=7) > current:
            activeRecords.append(getDNSName(key))
    for key, record in dns_cache.items():
        if key in activeRecords:
            continue
        if record["remark"]:
            continue
        r = dc.post('/Record.Remove', data={'domain': DOMAIN, 'record_id': record['id']})
        print(r.json())
        print(f'==> Delete Record {key}')

    refreshDNSCache()
    print("end to clean DNS")


lastCleanTime = datetime.now()
cleanLock = RLock()


def cleanDNS():
    global lastCleanTime
    global cleanLock
    with cleanLock:
        current = datetime.now()
        if lastCleanTime + timedelta(days=7) > current:
            return
        lastCleanTime = current
    doCleanDNS(current)


MY_IP = None
MY_DOMAIN_ID = None


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
        if MY_DOMAIN and key == MY_DOMAIN:
            MY_IP = r['value']
            MY_DOMAIN_ID = r['id']
    dns_cache = cache
    cache_update_time = time.time()


def getDNSName(hostId):
    return f'{hostId}.{SUB_DOMAIN}'


def getDNSDomain(hostId):
    return f'{hostId}.{SUB_DOMAIN}.{DOMAIN}'


refreshLock = RLock()


def getDNSValue(host):
    global dns_cache
    global cache_update_time
    global refreshLock
   # print(f'=== {cache_update_time}')
    with refreshLock:
        if dns_cache is None or time.time() > cache_update_time + CACHE_EXP_IN_SEC:
            refreshDNSCache()
    record = dns_cache.get(getDNSName(host))
    return record


def registerMyDomain():
    global MY_IP
    global MY_DOMAIN_ID
    try:
        if not MY_IP:
            refreshDNSCache()
        myip = get_ip()
        print(f'==> Updating {MY_DOMAIN} from {MY_IP} to {myip} ')
        if MY_DOMAIN_ID and MY_IP != myip:
            r = dc.post('/Record.Modify', data={'domain': DOMAIN, 'record_id': MY_DOMAIN_ID, 'record_type': 'A',
                                                'sub_domain': MY_DOMAIN, 'record_line': '默认', 'value': myip})
            print(r.json())
            MY_IP = myip
    except Exception:
        print(traceback.format_exc())
        MY_IP = None
    finally:
        threading.Timer(3600, registerMyDomain).start()


def main():
    if MY_DOMAIN:
        registerMyDomain()
    app.run(host="0.0.0.0", port=HTTP_PORT)


if __name__ == '__main__':
    main()
