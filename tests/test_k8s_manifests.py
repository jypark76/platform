# In plain English: these tests check the Kubernetes files BEFORE they reach a
# cluster. They build the final files for the laptop settings (the same thing
# "deploy.sh --preview" shows) and check them against a short list of rules, each
# one written because of something that went wrong earlier on the other repos or
# that we decided.
#
# A checker that never complains proves nothing, so the second half of the file
# feeds the checker deliberately broken files and confirms it complains about each
# one. Those self-tests need no Kubernetes tools and always run.
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "kafka" / "overlays" / "local"
DEPLOY_SCRIPT = ROOT / "kafka" / "deploy.sh"

NAMESPACE = "kafka"

# Kinds that do not live inside a namespace.
CLUSTER_WIDE = {"Namespace"}

# Kinds that run containers. Every rule about pods applies to all of them.
WORKLOADS = {"Deployment", "StatefulSet", "Job"}

# Things the pods point at that are NOT in the rendered files on purpose. Kafka
# needs none locally, so the list is empty.
MADE_OUTSIDE = set()

# Where the broker keeps its messages. It must be on a volume, or a restart of the
# pod would lose every message.
DATA_PATH = "/var/lib/kafka/data"


# In plain English: builds the final Kubernetes files for the laptop settings and
# returns them as a list of Python dictionaries, one per object.
def render_overlay():
    result = subprocess.run(
        ["kubectl", "kustomize", str(OVERLAY)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


# In plain English: the pod description inside a Deployment, StatefulSet or Job.
def pod_of(workload):
    return workload["spec"]["template"]["spec"]


# In plain English: every container of a workload, including the ones that run
# first to prepare things (init containers). A rule that skipped those could be
# dodged by hiding a bad container there.
def containers_of(workload):
    pod = pod_of(workload)
    return pod.get("initContainers", []) + pod.get("containers", [])


# In plain English: lists every ConfigMap and Secret that a workload points at,
# as (kind, name) pairs. It looks in all the places a pod can point at one:
# settings, settings from a whole ConfigMap, and mounted folders.
def references_of(workload):
    found = set()
    pod = pod_of(workload)
    for container in containers_of(workload):
        for env in container.get("env", []):
            source = env.get("valueFrom", {})
            if "secretKeyRef" in source:
                found.add(("Secret", source["secretKeyRef"]["name"]))
            if "configMapKeyRef" in source:
                found.add(("ConfigMap", source["configMapKeyRef"]["name"]))
        for entry in container.get("envFrom", []):
            if "configMapRef" in entry:
                found.add(("ConfigMap", entry["configMapRef"]["name"]))
            if "secretRef" in entry:
                found.add(("Secret", entry["secretRef"]["name"]))
    for volume in pod.get("volumes", []):
        if "configMap" in volume:
            found.add(("ConfigMap", volume["configMap"]["name"]))
        if "secret" in volume:
            found.add(("Secret", volume["secret"]["secretName"]))
        for source in volume.get("projected", {}).get("sources", []):
            if "configMap" in source:
                found.add(("ConfigMap", source["configMap"]["name"]))
            if "secret" in source:
                found.add(("Secret", source["secret"]["name"]))
    return found


# In plain English: the checker. It takes the list of Kubernetes objects and
# returns a list of plain sentences, one per rule that is broken. An empty list
# means every rule holds.
def find_problems(docs):
    problems = []
    defined = {(doc["kind"], doc["metadata"]["name"]) for doc in docs}

    for doc in docs:
        kind = doc["kind"]
        name = doc["metadata"]["name"]
        label = f"{kind}/{name}"

        # Rule 1: everything except the Namespace itself sits in our namespace.
        # A generated ConfigMap once had none and landed in the wrong room.
        if kind not in CLUSTER_WIDE and doc["metadata"].get("namespace") != NAMESPACE:
            problems.append(f"{label}: missing namespace '{NAMESPACE}'")

        # Rule 2: no Secret is ever stored in the repo, not even a template.
        if kind == "Secret":
            problems.append(f"{label}: a Secret must never be in the repo")

        # Rule 3: nothing is reachable from outside the cluster.
        if kind == "Service" and doc["spec"].get("type", "ClusterIP") != "ClusterIP":
            problems.append(f"{label}: service type must be ClusterIP")

        if kind not in WORKLOADS:
            continue

        pod = pod_of(doc)

        # Rule 4: the pod runs as a normal user, never as root.
        pod_security = pod.get("securityContext", {})
        if pod_security.get("runAsNonRoot") is not True:
            problems.append(f"{label}: runAsNonRoot must be true")
        run_as = pod_security.get("runAsUser")
        if not isinstance(run_as, int) or run_as == 0:
            problems.append(f"{label}: runAsUser must be a non-zero number")

        # Rule 5: Kubernetes must not inject settings for every Service in the
        # namespace into the pod. The Service called "kafka" would otherwise create a
        # KAFKA_PORT variable that breaks the Kafka image's own settings.
        if pod.get("enableServiceLinks") is not False:
            problems.append(f"{label}: enableServiceLinks must be false")

        for container in containers_of(doc):
            image = container.get("image", "")
            last_part = image.split("/")[-1]

            # Rule 6: a fixed version tag, never "latest" and never none.
            if ":" not in last_part:
                problems.append(f"{label}: image '{image}' has no version tag")
            elif last_part.endswith(":latest"):
                problems.append(f"{label}: image '{image}' must not use the tag latest")

            # Rule 7: a memory limit, so one pod cannot eat the whole machine.
            if not container.get("resources", {}).get("limits", {}).get("memory"):
                problems.append(f"{label}: container '{container['name']}' has no memory limit")

            # Rule 8: no extra privileges.
            security = container.get("securityContext", {})
            if security.get("allowPrivilegeEscalation") is not False:
                problems.append(f"{label}: allowPrivilegeEscalation must be false")
            if "ALL" not in security.get("capabilities", {}).get("drop", []):
                problems.append(f"{label}: capabilities must drop ALL")

        # Rule 9: every ConfigMap or Secret a pod points at must exist in the
        # files, or be one of the few things made outside them on purpose.
        for reference in sorted(references_of(doc)):
            if reference not in defined and reference not in MADE_OUTSIDE:
                problems.append(f"{label}: points at {reference[0]} '{reference[1]}', which does not exist")

    return problems


# ---------------------------------------------------------------------------
# Tests on the REAL files. These need the kubectl tool.
# ---------------------------------------------------------------------------


# In plain English: skips when kubectl is not installed, so a normal run on a
# computer without it still works. In the pipeline REQUIRE_KUBECTL=1 is set, and
# then a missing kubectl is a failure, because a skipped check proves nothing.
@pytest.fixture
def real_docs():
    if shutil.which("kubectl") is None:
        if os.environ.get("REQUIRE_KUBECTL") == "1":
            pytest.fail("REQUIRE_KUBECTL is set but kubectl was not found")
        pytest.skip("kubectl is not installed")
    return render_overlay()


# In plain English: the real laptop files must break none of the rules. It also
# checks the render really produced our objects, so an empty result can never pass.
def test_real_overlay_breaks_no_rules(real_docs):
    kinds = {(doc["kind"], doc["metadata"]["name"]) for doc in real_docs}
    assert ("Namespace", "kafka") in kinds
    assert ("StatefulSet", "kafka") in kinds
    assert ("Service", "kafka") in kinds
    assert ("Service", "kafka-headless") in kinds
    assert ("Job", "kafka-create-topics") in kinds
    assert find_problems(real_docs) == []


# In plain English: the job that creates the topic must be safe to run again.
# "--if-not-exists" means a second run finds the topic already there and does
# nothing, so deploying twice never fails. The job must also name our topic.
def test_the_topic_job_is_safe_to_run_again(real_docs):
    job = next(doc for doc in real_docs if doc["kind"] == "Job")
    words = []
    for container in containers_of(job):
        words.extend(str(word) for word in container.get("command", []))
        words.extend(str(word) for word in container.get("args", []))
    text = " ".join(words)
    assert "--if-not-exists" in text
    assert "approved-examples" in text


# In plain English: the text of every command the topic job runs, as one string.
def topic_job_text(real_docs):
    job = next(doc for doc in real_docs if doc["kind"] == "Job")
    return " ".join(
        str(word)
        for container in containers_of(job)
        for word in container.get("command", []) + container.get("args", [])
    )


# In plain English: reads each "--create ... --topic NAME --partitions N ... retention.ms=MS"
# command and returns {NAME: (lanes, days kept)}. A topic created twice is an error, so it
# is reported by the length check in the test below.
def topics_created(text):
    found = {}
    for command in text.split("--create")[1:]:
        name = re.search(r"--topic (\S+)", command).group(1)
        lanes = int(re.search(r"--partitions (\d+)", command).group(1))
        days = int(re.search(r"retention\.ms=(\d+)", command).group(1)) // 86_400_000
        assert name not in found, f"topic {name} is created twice"
        found[name] = (lanes, days)
    return found


# In plain English: the job must create exactly the topics we agreed, with the agreed number
# of lanes and days kept. Approved examples go to the knowledge service. Grading jobs go to
# the grader (6 lanes, because grading is the slow step and the lanes cap how many workers
# can work at once). Grading results come back to the assessment service. Each has a
# dead-letter topic for messages that can never be handled, kept 30 days so a person can look.
def test_the_topic_job_creates_exactly_the_agreed_topics(real_docs):
    text = topic_job_text(real_docs)
    assert text.count("--create") == 6
    assert text.count("--if-not-exists") == text.count("--create")
    assert topics_created(text) == {
        "approved-examples": (3, 7),
        "approved-examples.dead-letter": (1, 30),
        "grading-jobs": (6, 7),
        "grading-jobs.dead-letter": (1, 30),
        "grading-results": (3, 7),
        "grading-results.dead-letter": (1, 30),
    }


# In plain English: whatever topics exist, every dead-letter topic must belong to a main
# topic, every main topic must have one, and the dead-letter topic must have 1 lane and be
# kept longer, so a person has time to look at what went wrong.
def test_every_topic_has_a_longer_kept_dead_letter_topic(real_docs):
    topics = topics_created(topic_job_text(real_docs))
    mains = {name for name in topics if not name.endswith(".dead-letter")}
    dead = {name for name in topics if name.endswith(".dead-letter")}
    assert mains, "no topics found"
    assert {f"{name}.dead-letter" for name in mains} == dead
    for name in mains:
        lanes, days = topics[f"{name}.dead-letter"]
        assert lanes == 1
        assert days > topics[name][1]


# In plain English: every topic the job creates must be described in a written
# contract (a file in the contracts folder). A topic with no written format is how
# the sender and the reader end up disagreeing about what a message looks like.
def test_every_topic_has_a_written_contract(real_docs):
    job = next(doc for doc in real_docs if doc["kind"] == "Job")
    text = " ".join(
        str(word)
        for container in containers_of(job)
        for word in container.get("command", []) + container.get("args", [])
    )
    topics = re.findall(r"--topic (\S+)", text)
    assert len(topics) == 6
    contracts = " ".join(path.read_text(encoding="utf-8") for path in (ROOT / "contracts").glob("*.md"))
    for topic in topics:
        assert f"`{topic}`" in contracts, f"no written contract mentions the topic {topic}"


# In plain English: the broker must keep its messages on a volume. Without one, a
# restart of the pod would wipe every message. This checks the volume exists, is
# mounted at the data folder, and that Kafka is told to write there.
def test_the_messages_survive_a_restart(real_docs):
    broker = next(doc for doc in real_docs if doc["kind"] == "StatefulSet")
    assert broker["spec"].get("volumeClaimTemplates")
    container = containers_of(broker)[0]
    mounts = {mount["mountPath"] for mount in container.get("volumeMounts", [])}
    assert DATA_PATH in mounts
    settings = {env["name"]: env.get("value") for env in container.get("env", [])}
    assert settings.get("KAFKA_LOG_DIRS") == DATA_PATH


# In plain English: the deploy script must never hide errors by sending them to
# the "bit bucket". That once made a real error look like "Secret missing".
def test_deploy_script_does_not_hide_errors():
    assert "/dev/null" not in DEPLOY_SCRIPT.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Self-tests: the checker must complain about broken files. No kubectl needed.
# ---------------------------------------------------------------------------


# In plain English: a pod description that breaks no rule. Each self-test copies
# the good files and breaks exactly one thing.
def good_pod(name, image):
    return {
        "enableServiceLinks": False,
        "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "fsGroup": 1000},
        "containers": [
            {
                "name": name,
                "image": image,
                "resources": {"limits": {"memory": "1Gi"}},
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                },
            }
        ],
    }


# In plain English: a small set of Kubernetes objects that breaks no rule: the
# namespace, a service, the broker and the topic job.
def good_docs():
    return [
        {"kind": "Namespace", "metadata": {"name": NAMESPACE}},
        {
            "kind": "Service",
            "metadata": {"name": "kafka", "namespace": NAMESPACE},
            "spec": {"ports": [{"port": 9092}]},
        },
        {
            "kind": "StatefulSet",
            "metadata": {"name": "kafka", "namespace": NAMESPACE},
            "spec": {"template": {"spec": good_pod("kafka", "apache/kafka:3.9.0")}},
        },
        {
            "kind": "Job",
            "metadata": {"name": "kafka-create-topics", "namespace": NAMESPACE},
            "spec": {"template": {"spec": good_pod("create-topics", "apache/kafka:3.9.0")}},
        },
    ]


# In plain English: the control. Before trusting any "broken" case, the good
# files must come back clean, or a complaint could mean anything.
def test_good_files_come_back_clean():
    assert find_problems(good_docs()) == []


def of_kind(docs, kind):
    return next(doc for doc in docs if doc["kind"] == kind)


def test_catches_a_missing_namespace():
    docs = good_docs()
    del docs[1]["metadata"]["namespace"]
    assert any("missing namespace" in problem for problem in find_problems(docs))


def test_catches_a_secret_in_the_repo():
    docs = good_docs()
    docs.append({"kind": "Secret", "metadata": {"name": "oops", "namespace": NAMESPACE}})
    assert any("Secret must never be in the repo" in problem for problem in find_problems(docs))


def test_catches_the_latest_tag():
    docs = good_docs()
    containers_of(of_kind(docs, "StatefulSet"))[0]["image"] = "apache/kafka:latest"
    assert any("latest" in problem for problem in find_problems(docs))


def test_catches_a_missing_tag():
    docs = good_docs()
    containers_of(of_kind(docs, "StatefulSet"))[0]["image"] = "apache/kafka"
    assert any("no version tag" in problem for problem in find_problems(docs))


def test_catches_a_service_open_to_the_outside():
    docs = good_docs()
    docs[1]["spec"]["type"] = "NodePort"
    assert any("ClusterIP" in problem for problem in find_problems(docs))


def test_catches_a_missing_memory_limit():
    docs = good_docs()
    containers_of(of_kind(docs, "StatefulSet"))[0]["resources"] = {}
    assert any("memory limit" in problem for problem in find_problems(docs))


def test_catches_a_pointer_to_something_that_does_not_exist():
    docs = good_docs()
    containers_of(of_kind(docs, "StatefulSet"))[0]["envFrom"] = [{"configMapRef": {"name": "no-such-settings"}}]
    assert any("does not exist" in problem for problem in find_problems(docs))


def test_catches_a_pod_that_may_run_as_root():
    docs = good_docs()
    pod_of(of_kind(docs, "StatefulSet"))["securityContext"] = {}
    problems = find_problems(docs)
    assert any("runAsNonRoot" in problem for problem in problems)
    assert any("runAsUser" in problem for problem in problems)


def test_catches_privilege_escalation_and_kept_capabilities():
    docs = good_docs()
    security = containers_of(of_kind(docs, "StatefulSet"))[0]["securityContext"]
    security["allowPrivilegeEscalation"] = True
    security["capabilities"] = {"drop": []}
    problems = find_problems(docs)
    assert any("allowPrivilegeEscalation" in problem for problem in problems)
    assert any("drop ALL" in problem for problem in problems)


def test_catches_service_links_left_on():
    docs = good_docs()
    del pod_of(of_kind(docs, "StatefulSet"))["enableServiceLinks"]
    assert any("enableServiceLinks" in problem for problem in find_problems(docs))


# In plain English: the rules must cover the topic job too, not only the broker.
# A checker that looked only at the StatefulSet would let a bad job through.
def test_catches_a_problem_in_the_job():
    docs = good_docs()
    containers_of(of_kind(docs, "Job"))[0]["image"] = "apache/kafka:latest"
    problems = find_problems(docs)
    assert any("Job/kafka-create-topics" in problem and "latest" in problem for problem in problems)


# In plain English: the rules must cover init containers too, the ones that run
# first. Otherwise a bad container could hide there.
def test_catches_a_problem_in_an_init_container():
    docs = good_docs()
    pod = pod_of(of_kind(docs, "StatefulSet"))
    init = dict(pod["containers"][0], name="prepare", image="busybox")
    pod["initContainers"] = [init]
    assert any("busybox" in problem and "no version tag" in problem for problem in find_problems(docs))
