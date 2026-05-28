#!/usr/bin/env bash
# ADR-073: emitter.tool_display_config must only be written in _base_outbound.py
# Pre-push gate — analogous to check_outbound_str_exc.sh

hits=$(grep -rn "\.tool_display_config\s*=" src/ --include="*.py" | grep -v "_base_outbound.py:")
if [ -n "$hits" ]; then
    echo "ADR-073 violation: emitter.tool_display_config = … must only appear in _base_outbound.py"
    echo "$hits"
    exit 1
fi
