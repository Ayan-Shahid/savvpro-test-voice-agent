#!/bin/sh
# Render ACL users from secrets (the API and the worker each get only what they need), then hand over to the
# official image entrypoint, which gives /data to the `redis` user and runs redis-server as that user.
set -eu
umask 022
api=$(cat /run/secrets/redis_api_password); worker=$(cat /run/secrets/redis_worker_password)
cat > /tmp/users.acl <<ACL
user default off
user astro-api on >$api ~av:* ~_kombu.* ~waitlist ~email ~maintenance ~unacked* &av:events:* -@all +@read +@write +@string +@hash +@list +@set +@keyspace +@pubsub +@transaction +@scripting +@connection +ping -@dangerous
user astro-worker on >$worker ~* &* -@all +@read +@write +@list +@string +@hash +@set +@sortedset +@keyspace +@pubsub +@transaction +@scripting +@connection +ping +info -@dangerous
ACL
chmod 0444 /tmp/users.acl
echo "start.sh: ACL rendered; starting redis-server through the image entrypoint" >&2
exec docker-entrypoint.sh redis-server /usr/local/etc/redis/redis.conf
