# Platform

The shared pieces that several services of the AI assessment grader use. The services
themselves live in their own repos: `knowledge-service` and `assessment-service`.

**Status: just started.** Nothing is deployed from this repo yet.

## What it will hold

- **Kafka**, a message queue that sits between the services. It comes first. For now it
  is one real Apache Kafka broker for the Docker Desktop cluster on a laptop.
- Later: the autoscaler for the grader workers (KEDA) and the AWS setup.

## Rules

- This repo is public. It never contains passwords, API keys or Kubernetes Secret
  files, not even templates.
- Every change goes through a pull request with passing checks.
