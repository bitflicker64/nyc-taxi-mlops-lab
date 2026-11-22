# 014: A keep-list per source, and no label that changes on deploy

Stage: S5 · Date: 2026-11-22 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

`observability/alloy/budget.alloy` keeps only series a dashboard panel, an SLO or an incident uses:

- **Host:** CPU seconds by mode, available and total memory, real disks' free and total size, the host interface's bytes in and out, load1, `up`. Not loopback, Docker bridges or veths, nor tmpfs or EFI.
- **Containers:** only named containers. CPU, working-set memory, memory limit, start time (restarts), OOM kills, `up`. The labels `id`, `image` and `container_label_*` are dropped.
- **Services:** `lab_*` (contracts/metrics.yaml), `traefik_service_*` (S8's canary), `up`. Runtime metrics (`go_*`, `python_*`, `process_*`) go.

Budget: stay under **2,500 active series** for all hosts for the rest of the lab, a quarter of the free tier's 10,000.

## Rejected alternative

A drop-list: send everything except known junk (cAdvisor filesystem metrics, netstat and sockstat, runtime metrics).

## Why

- **Before any rules, two hosts sent 3,446 series.** About 1,700 were cAdvisor metrics, mostly 18 filesystem ones per container. About 1,300 were node_exporter, 239 of them network detail. The dashboard used 9 metric names.
- **A keep-list fails safe.** Something new (a new container, an exporter upgrade, S6's LLM host) adds a few series, not hundreds. A drop-list lets every new metric through until someone notices the bill.
- **Labels that change on deploy multiply series, not just add them.** cAdvisor's `id` (container id), `image` (`…:sha-<commit>`) and Compose's config hash are new on every deploy. Each new value starts a new series while the old one is still active, so three deploys in a row tripled every container's series. `name` (`lab-app-app-green-1`) is stable across deploys and is what the dashboard groups by.
- **Unnamed cgroups go** (the root and system slices): node_exporter already covers the host as a whole, and without `id` they would collide.
- **2,500, not 9,000:** S6 adds a third host, gameday incidents add series (restarts, new versions), and a budget run close to the limit gets samples refused exactly when you most need them.
- **The cost I accept:** in an incident, the metric I need may be one I dropped (disk I/O, network errors, GC pauses). Adding it back is one regex and a push.

## What would show this was wrong

- An incident where the cause was visible only in a dropped metric, more than once.
- Active series grow with every deploy anyway: then another label is changing, and it belongs in the labeldrop.
- The count stays far under 1,000 through S6: then the budget could afford more detail for host disk and network.
