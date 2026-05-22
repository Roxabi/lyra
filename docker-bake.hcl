group "default" {
  targets = ["agent-runtime", "svc-runtime"]
}

target "agent-runtime" {
  dockerfile = "Dockerfile"
  context    = "."
  target     = "agent-runtime"
  cache-from = ["type=gha,scope=lyra-agent"]
  cache-to   = ["type=gha,mode=max,scope=lyra-agent"]
}

target "svc-runtime" {
  dockerfile = "Dockerfile"
  context    = "."
  target     = "svc-runtime"
  cache-from = ["type=gha,scope=lyra-svc"]
  cache-to   = ["type=gha,mode=max,scope=lyra-svc"]
}
