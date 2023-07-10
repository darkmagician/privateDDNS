# privateDDNS

## Introduction

It is used for DDNS management in private network. The agent accepts the request from local network and update the DNS server(DNSPOD) if the ip address is changed.



## Usage



### Requirements

```
pip install -r requirements
```



### Run

export the environment and run

```shell
export DNSPOD_TOKEN_ID = xxx
export DNSPOD_TOKEN = 'xxx'
export DOMAIN = 'xxx'
export SUB_DOMAIN = 'xxx'
```



```shell
python pddns.py
```



### Client Call

Run the command at the client side periodically.

```shell
curl -X POST <server ip>:3053/hosts/<hostId>
```

Get hosts

```
curl <server ip>:3053/hosts
```

Get domains

```
curl <server ip>:3053/domains
```





## Docker Run

build docker

```
docker build -t pddns .
```



create a env.txt with the following content

```
DNSPOD_TOKEN_ID = xxx
DNSPOD_TOKEN = 'xxx'
DOMAIN = 'xxx'
SUB_DOMAIN = 'xxx'
```

start docker

```
docker run -d --net=host --env-file=env.txt pddns
```



