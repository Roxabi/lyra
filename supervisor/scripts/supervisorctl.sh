#!/bin/bash
exec supervisorctl -c "$HOME/supervisor/supervisord.conf" "$@"
