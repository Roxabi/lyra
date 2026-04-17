#!/usr/bin/env bash
# Wrapper for voicecli_stt / voicecli_tts daemons — sources .env before launching.
# Usage: run_voicecli.sh {stt|tts}
# supervisor conf points to this script so secrets never live in conf files.
set -a
[ -f "$HOME/projects/voiceCLI/.env" ] && source "$HOME/projects/voiceCLI/.env"
set +a

case "$1" in
    stt|tts) ;;
    *)
        echo "run_voicecli.sh: invalid argument '$1' — expected stt or tts" >&2
        exit 2
        ;;
esac

exec "$HOME/projects/voiceCLI/.venv/bin/voicecli" nats-serve "$1"
