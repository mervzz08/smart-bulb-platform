# Running the bridge over MQTTS

The bridge supports TLS to the broker out of the box (`MQTT_TLS=true`), with optional pinning to a private CA (`MQTT_CA_FILE`). Hostname verification stays on, and the minimum protocol is TLS 1.2.

> **What's verified:** the bridge's TLS settings (TLS 1.2 minimum, certificate and hostname checks required) are unit-tested. The automated end-to-end run uses plain MQTT. Follow the steps below to try TLS by hand.

## 1. Create a private CA and server certificate

```sh
mkdir -p deploy/mosquitto/certs && cd deploy/mosquitto/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout ca.key -out ca.crt -subj "/CN=bulb-demo-ca"
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=mosquitto"
printf "subjectAltName=DNS:mosquitto,DNS:localhost" > san.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 -out server.crt -extfile san.ext
```

The SAN must match the hostname the bridge connects to (`mosquitto` inside compose, `localhost` from your machine). Otherwise the bridge refuses the connection, which is the point of hostname checking.

## 2. Create broker users

```sh
docker run --rm -v "$PWD:/w" eclipse-mosquitto:2.0.18 mosquitto_passwd -b -c /w/passwords bridge 'change-me'
```

## 3. Point the broker and bridge at TLS

In `docker-compose.yml`, mount `mosquitto-tls.conf`, `certs/` and `passwords` into the broker. Then give the bridge:

```env
MQTT_TLS=true
MQTT_PORT=8883
MQTT_CA_FILE=/certs/ca.crt
MQTT_USERNAME=bridge
MQTT_PASSWORD=change-me
```

## Bulbs and TLS

The ESP8266 can do TLS (BearSSL), but a TLS session takes about 20–25 KB of its ~80 KB of RAM. The usual setup:
- Bulbs talk plain MQTT to a broker on a separate IoT VLAN.
- The bridge talks MQTTS whenever traffic leaves that network.

An ESP32 port would be the natural place to move bulbs to TLS as well.
