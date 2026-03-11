#!/bin/bash
exec supervisorctl -c "$HOME/lyra-stack/supervisord.conf" "$@"
