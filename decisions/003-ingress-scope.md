# 003: Ingress scope: my IP and the grader's, nothing else

Stage: S0 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

Port 80 on the app host accepts connections only from two `/32` CIDRs in `terraform.tfvars` (`ingress_cidrs`): my current public IP and the grader's IP from the instructor. No other port on any host is open to the internet. Traffic between hosts is allowed by security group, not IP, and only on the ports in `contracts/runtime.yaml`.

## Rejected alternative

`0.0.0.0/0` on port 80, so the app works from any network.

## Why

- The API has no authentication, and from S6 `/assist` runs an LLM on my hosts. A public port is found by scanners within hours, and every request uses my lab budget and pollutes the metrics I'll set SLOs on in S5.
- The cost I accept: when my IP changes (a new network, a new ISP lease), I edit `terraform.tfvars` and `terraform apply`. That changes one security group rule and replaces nothing else.

## What would show this was wrong

- I have to change the rule more than about once a day, or the grader can't reach the app because their IP changed.
- A real user group has to reach the app. Then the answer is authentication in front of the app, not a wider CIDR.
