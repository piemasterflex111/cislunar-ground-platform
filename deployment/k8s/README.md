# Kubernetes reference deployment

This directory demonstrates how the application services are deployed with probes, resource limits, rolling updates, and pod anti-affinity. It intentionally excludes production TLS, external secrets, ingress policy, and managed database/broker choices.

```bash
kubectl apply -f namespace.yaml
kubectl apply -f config.yaml
kubectl apply -f data-services.yaml
kubectl apply -f application-services.yaml
```

Replace `ghcr.io/OWNER/cislunar-ground-platform:latest` with your built image and replace every example secret before use.
