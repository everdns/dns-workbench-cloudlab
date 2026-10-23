#!/bin/sh
# Start the BIND version named in /opt/bind-sweep/current.
# Called by max_sustainable_qps.py as "<start_script> ns_bind"; the argument is ignored.
V=$(cat /opt/bind-sweep/current) || exit 1
sudo systemctl stop named 2>/dev/null
sudo pkill -x named 2>/dev/null
sleep 1
sudo "/opt/bind-$V/sbin/named" -c "/etc/bind-$V/named.conf"
