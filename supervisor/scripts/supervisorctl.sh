#!/bin/bash
exec supervisorctl -c "$HOME/projects/lyra-stack/supervisord.conf" "$@"
