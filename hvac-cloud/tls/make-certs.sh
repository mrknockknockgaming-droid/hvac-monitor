#!/bin/sh
# Makes the broker's TLS certificate, signed by a private CA that the node firmware trusts.
# The CA (ca.crt, ca.key) is made once and kept; run again to renew server.crt (valid 5 years).
#   docker run --rm -v "$PWD/tls:/tls" alpine:3 sh /tls/make-certs.sh mqtt.example.com
# Keep ca.key secret and backed up: losing it means re-flashing every node with a new CA.
set -e
name="${1:?usage: make-certs.sh <domain or IP the nodes connect to>}"
command -v openssl >/dev/null || apk add --no-cache openssl >/dev/null
cd "$(dirname "$0")"

if [ ! -f ca.key ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 7300 \
    -subj "/O=Fullscope/CN=Fullscope MQTT CA" 2>/dev/null
  echo "made a new CA (ca.crt goes into the node firmware)"
fi

case "$name" in
  *[!0-9.]*) san="DNS:$name" ;;
  *)         san="IP:$name" ;;
esac
printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n" "$san" > ext.cnf
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/O=Fullscope/CN=$name" 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 1825 \
  -extfile ext.cnf 2>/dev/null
rm -f server.csr ext.cnf
chmod 600 ca.key server.key
echo "server.crt for $name ($san), valid 5 years"
