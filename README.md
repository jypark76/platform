# Platform

The shared pieces that several services of the AI assessment grader use. The services
themselves live in their own repos: `knowledge-service` and `assessment-service`.

**Status: early.** Kafka runs on a laptop cluster. The assessment service sends approved
examples through it and the knowledge service reads them. The grading topics are created
but not used yet.

## What it holds

- **Kafka**, a message queue that sits between the services. One real Apache Kafka
  broker (the official `apache/kafka` image, version 3.9.0, no ZooKeeper) in its own
  `kafka` namespace on the Docker Desktop cluster. It keeps its messages on a 1 GB saved
  disk, so a restart does not lose them. A one-time job creates the topics, three pairs of a topic and its dead-letter
  topic (where a reader puts a message it can never handle, with a reason, kept 30 days):

  | Topic | Written by | Read by | Lanes | Kept |
  |---|---|---|---|---|
  | `approved-examples` | assessment service | knowledge service | 3 | 7 days |
  | `grading-jobs` | assessment service | grader worker | 6 | 7 days |
  | `grading-results` | grader worker | assessment service | 3 | 7 days |

  Each has a `.dead-letter` topic with 1 lane. The lanes are the most readers that can work
  at once, so `grading-jobs` (grading is the slow step) has the most. Only the first pair is
  in use so far; the grading pairs are ready but nothing reads or writes them yet.
- **Contracts**, in [`contracts/`](contracts): the agreed format of each message, shared
  by the service that sends it and the service that reads it. Every topic has one, and a
  test checks that none is missing.
- Later: the autoscaler for the grader workers (KEDA) and the AWS setup.

## Deploying Kafka on the laptop

```
bash kafka/deploy.sh --preview   # show what would be deployed
bash kafka/deploy.sh             # deploy
```

The script looks at which cluster `kubectl` points at and applies the matching settings.
It refuses any cluster it does not recognise. Kafka needs no password locally, so there
is no Secret to create.

Other pods reach it at `kafka.kafka.svc.cluster.local:9092`.

To check it works, put messages in and read them back (in Git Bash on Windows, put
`MSYS_NO_PATHCONV=1` before `kubectl exec`, or the paths get rewritten):

```
printf 'one\ntwo\nthree\n' | kubectl -n kafka exec -i kafka-0 -- /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server localhost:9092 --topic approved-examples
kubectl -n kafka exec kafka-0 -- /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic approved-examples --from-beginning --max-messages 3
```

To start from scratch (this deletes every message):

```
kubectl -n kafka delete statefulset/kafka pvc/data-kafka-0
bash kafka/deploy.sh
```

## Tests

```
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -v
```

`tests/test_k8s_manifests.py` builds the Kubernetes files for the laptop settings and
checks them against our rules: the right namespace, no Secret, a fixed image version,
a memory limit, no root user, and the data kept on a saved disk. It needs `kubectl` and
the real-file checks are skipped without it. Its self-tests feed it deliberately broken
files, so it cannot pass by never complaining. The same tests run on every pull request,
and the pipeline runs every test file, never a list.

## Known limits

- **One broker.** There is no second copy of any message, so losing the broker means
  downtime. On AWS this would be several brokers, run by an operator such as Strimzi or
  by Amazon's managed Kafka (MSK). The files here are the laptop version.
- **No login and no encryption.** Any pod in the cluster could publish or read. A
  NetworkPolicy that narrows this is not written yet, and it could not be proven on
  Docker Desktop, which does not enforce it. On AWS this needs TLS and a login.
- **Laptop only.** There are no settings for AWS yet, on purpose.

## Rules

- This repo is public. It never contains passwords, API keys or Kubernetes Secret
  files, not even templates.
- Every change goes through a pull request with passing checks.
