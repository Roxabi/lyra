group "default" {
  targets = ["agent-runtime", "svc-runtime"]
}

target "agent-runtime" {
  dockerfile = "Dockerfile"
  context    = "."
  target     = "agent-runtime"
  cache-from = ["type=gha,scope=factory-agent"]
  cache-to   = ["type=gha,mode=max,scope=factory-agent"]
}

target "svc-runtime" {
  dockerfile = "Dockerfile"
  context    = "."
  target     = "svc-runtime"
  cache-from = ["type=gha,scope=factory-svc"]
  cache-to   = ["type=gha,mode=max,scope=factory-svc"]
}
