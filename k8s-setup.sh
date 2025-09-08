#!/bin/bash

kind create cluster --config k8s/0-kind.yaml
docker build -t ipam-api:latest .
kind load docker-image ipam-api:latest
kubectl apply -f k8s/1-namespace.yaml
kubectl apply -f k8s/2-secret.yaml
kubectl apply -f k8s/3-postgres.yaml
echo "Waiting 10 seconds for Postgres to initialize..."
sleep 10
kubectl apply -f k8s/4-api.yaml
echo "Waiting another 5 seconds for the API to start..."
sleep 5
kubectl port-forward -n ipam svc/ipam-api 8080:80
