# Open Terminal API key

Create the runtime Secret once before Flux reconciles the Open Terminal Deployment. The API key value is intentionally not stored in Git:

```sh
kubectl -n openwebui create secret generic open-terminal-secret \
  --from-literal=OPEN_TERMINAL_API_KEY="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

The Deployment reads the key through `OPEN_TERMINAL_API_KEY_FILE` from this Secret.
