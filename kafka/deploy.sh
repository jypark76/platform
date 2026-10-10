#!/usr/bin/env bash
# In plain English: this script deploys Kafka to whichever Kubernetes cluster your
# computer is currently pointed at, using the right settings for that cluster. It
# works out the environment by itself:
#   - the Docker Desktop cluster on your laptop  -> the "local" settings
#   - an AWS EKS cluster                         -> the "aws" settings
#   - anything else                              -> it refuses and stops
# That last rule is the safety net: laptop settings can never be applied to a
# real cluster by accident, or the other way round. There are no "aws" settings
# yet, on purpose, so for now only the laptop works.
#
# Kafka needs no password locally, so this script never creates or reads a Secret.
#
# Usage:  bash kafka/deploy.sh            (deploy)
#         bash kafka/deploy.sh --preview  (only show what would be deployed)
set -euo pipefail

# Find the kafka folder no matter where the script is run from.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ask kubectl which cluster it is pointed at right now.
context="$(kubectl config current-context)"

# Pick the settings folder from the cluster's name.
case "$context" in
  docker-desktop)
    overlay="local"
    ;;
  arn:aws:eks:*)
    overlay="aws"
    ;;
  *)
    echo "Refusing to deploy: the cluster '$context' is not recognised." >&2
    echo "Expected 'docker-desktop' (laptop) or an AWS EKS cluster." >&2
    exit 1
    ;;
esac

# The settings folder must exist. The AWS one is not written yet, on purpose.
if [ ! -d "$here/overlays/$overlay" ]; then
  echo "Refusing to deploy: there are no '$overlay' settings yet (kafka/overlays/$overlay)." >&2
  exit 1
fi

echo "Cluster:  $context"
echo "Settings: $overlay"

# Preview mode: show the final result and stop, changing nothing.
if [ "${1:-}" = "--preview" ]; then
  kubectl kustomize "$here/overlays/$overlay"
  exit 0
fi

# Deploying to AWS costs money and is visible to others, so ask first.
if [ "$overlay" = "aws" ]; then
  read -r -p "This is a real AWS cluster. Type 'yes' to continue: " answer
  [ "$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
fi

# Make sure the "kafka" room exists before anything is put in it.
kubectl apply -f "$here/base/namespace.yaml"

# A job cannot be changed after it is made, so remove the old topic job first. The
# job is safe to run again: it only creates the topic if it is missing.
kubectl delete job kafka-create-topics -n kafka --ignore-not-found

# Deploy everything for this environment.
kubectl apply -k "$here/overlays/$overlay"
