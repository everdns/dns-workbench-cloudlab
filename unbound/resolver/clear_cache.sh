#!/bin/sh
sudo killall unbound
sudo unbound -c /usr/local/etc/unbound/unbound.conf