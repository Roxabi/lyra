group "default" {
  targets = ["agent-runtime", "svc-runtime"]
}

# provenance/sbom disabled: buildx attestations embed per-build metadata (timestamps) into
# the pushed OCI index, so the index digest changed on EVERY build even when no code changed —
# which flipped the convergence fingerprint (fields 4/5) and forced a full-fleet restart on
# every commit. Disabling them (+ dropping the baked ROXABI_BUILD_REVISION file) makes the
# pushed digest content-addressed: same source → same digest (audit 2026-07-01, lot 4a).
target "agent-runtime" {
  dockerfile = "Dockerfile"
  context    = "."
  target     = "agent-runtime"
  provenance = false
  sbom       = false
  cache-from = ["type=gha,scope=factory-agent"]
  cache-to   = ["type=gha,mode=min,scope=factory-agent"]
}

target "svc-runtime" {
  dockerfile = "Dockerfile"
  context    = "."
  target     = "svc-runtime"
  provenance = false
  sbom       = false
  cache-from = ["type=gha,scope=factory-svc"]
  cache-to   = ["type=gha,mode=min,scope=factory-svc"]
}
